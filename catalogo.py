"""GIGA SCAN - catalogo das obras no Discord, via MangaDex + webhook.

Biblioteca padrao do Python 3.11+. Integra-se ao repositorio existente sem
alterar bot.py, state.json ou o workflow de lancamentos.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / 'catalogo_obras.json'
STATE_FILE = ROOT / 'catalogo_state.json'
API = 'https://api.mangadex.org'
UA = 'GigaScan-Catalogo-Discord/1.0 (public group catalogue; no downloads)'
PINK = 0xFF79B0
GROUP_DEFAULT = 'c26db585-798d-4da5-9d0f-8a0ffd141f50'

TAGS_PT = {
    'Romance': 'Romance', 'Comedy': 'Comédia', 'Drama': 'Drama',
    'Slice of Life': 'Cotidiano', 'School Life': 'Vida Escolar',
    'Supernatural': 'Sobrenatural', 'Fantasy': 'Fantasia',
    'Action': 'Ação', 'Adventure': 'Aventura', 'Mystery': 'Mistério',
    'Psychological': 'Psicológico', 'Horror': 'Terror',
    'Thriller': 'Suspense', 'Sci-Fi': 'Ficção científica',
    'Isekai': 'Isekai', 'Harem': 'Harém', 'Reincarnation': 'Reencarnação',
    'Time Travel': 'Viagem no tempo', 'Genderswap': 'Troca de gênero',
    'Historical': 'Histórico', 'Sports': 'Esportes', 'Magic': 'Magia',
    'Gore': 'Violência gráfica', 'Sexual Violence': 'Violência sexual',
    'Girls Love': 'Girls Love', 'Boys Love': 'Boys Love',
    'Adaptation': 'Adaptação', 'Long Strip': 'Formato vertical',
    'Full Color': 'Colorido', 'Web Comic': 'Webcomic',
}
STATUS_PT = {'ongoing': 'Em andamento', 'completed': 'Concluída',
             'hiatus': 'Em pausa', 'cancelled': 'Cancelada'}
RATING_PT = {'safe': 'Geral', 'suggestive': 'Sugestivo',
             'erotica': 'Erótico', 'pornographic': 'Adulto explícito'}
LINK_LABELS = [('mangadex', 'MangaDex'), ('sakura', 'Sakura'),
               ('capivara', 'Capivara'), ('kuro', 'Kuro'),
               ('site', 'Site oficial')]


def clean(value: object, limit: int = 4000) -> str:
    text = re.sub(r'\s+', ' ', html.unescape(str(value or '')))
    text = re.sub(r'<[^>]*>', '', text).strip()
    text = text.replace('@everyone', '@ everyone').replace('@here', '@ here')
    return text[:limit]


def md_text(value: object, limit: int = 2000) -> str:
    return re.sub(r'([\\\[\]`*~|<>])', r'\\\1', clean(value, limit))


def safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
            return None
        if any(ch in value for ch in '<>\n\r\t )('):
            return None
        return value[:1950]
    except ValueError:
        return None


def get_json(url: str, *, payload: dict | None = None, method: str = 'GET') -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'User-Agent': UA, 'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json; charset=utf-8'
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                raw = response.read()
                return json.loads(raw.decode('utf-8')) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                try:
                    retry_after = float(exc.headers.get('Retry-After', '0'))
                except (ValueError, TypeError):
                    retry_after = 0
                time.sleep(min(40, max(2 ** attempt + 1, retry_after)))
                continue
            # Nunca exibir a URL: no webhook, ela contem um token secreto.
            raise RuntimeError(f'Requisicao HTTP falhou: {exc.code} ({method}).') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt < 4:
                time.sleep(2 ** attempt + 1)
                continue
            raise RuntimeError(f'Falha de rede: {type(exc).__name__}.') from None
    raise RuntimeError('Tentativas de conexao esgotadas.')


def save_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    parsed = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(parsed, dict):
        raise RuntimeError(f'Arquivo invalido: {path.name}.')
    return parsed


def title_from_map(m: dict | None) -> str:
    m = m or {}
    for key in ('pt-br', 'en', 'ja-ro', 'ja'):
        if m.get(key):
            return clean(m[key], 200)
    return clean(next(iter(m.values()), 'Mangá sem título'), 200)


def chapter_manga_id(chapter: dict, group_id: str) -> str | None:
    relations = chapter.get('relationships') or []
    linked_group = [r.get('id') for r in relations if r.get('type') == 'scanlation_group']
    if group_id not in linked_group:
        return None
    return next((r.get('id') for r in relations if r.get('type') == 'manga'), None)


def fetch_group_manga_ids(group_id: str, max_pages: int) -> list[str]:
    # Consulta as publicacoes da propria scan; sem tentar detectar outros sites.
    ids: set[str] = set()
    offset = 0
    for page in range(max_pages):
        params = [('limit', '100'), ('offset', str(offset)),
                  ('groups[]', group_id), ('translatedLanguage[]', 'pt-br'),
                  ('order[publishAt]', 'desc'), ('includes[]', 'manga'),
                  ('contentRating[]', 'safe'), ('contentRating[]', 'suggestive'),
                  ('contentRating[]', 'erotica')]
        data = get_json(f'{API}/chapter?{urllib.parse.urlencode(params)}')
        if data.get('result') != 'ok' or not isinstance(data.get('data'), list):
            raise RuntimeError('Resposta invalida da API de capitulos do MangaDex.')
        chapters = data['data']
        for ch in chapters:
            found = chapter_manga_id(ch, group_id)
            if found:
                ids.add(found)
        offset += len(chapters)
        print(f'MangaDex: pagina {page + 1}, capitulos {offset}, obras distintas {len(ids)}.')
        total = data.get('total')
        if not chapters or (isinstance(total, int) and offset >= total) or len(chapters) < 100:
            break
        time.sleep(0.4)
    if len(chapters) == 100 and page + 1 == max_pages:
        print('AVISO: limite de paginas atingido; algumas obras muito antigas podem nao aparecer.')
    return sorted(ids)


def fetch_manga(manga_id: str) -> dict:
    params = urllib.parse.urlencode({'includes[]': 'cover_art'})
    response = get_json(f'{API}/manga/{manga_id}?{params}')
    result = response.get('data')
    if not isinstance(result, dict) or result.get('id') != manga_id:
        raise RuntimeError(f'Resposta inesperada do MangaDex para mangá {manga_id}.')
    attrs = result.get('attributes') or {}
    descs = attrs.get('description') or {}
    tags = []
    for tag in attrs.get('tags') or []:
        names = ((tag.get('attributes') or {}).get('name') or {})
        label = names.get('pt-br') or TAGS_PT.get(names.get('en')) or names.get('en')
        if label and label not in tags:
            tags.append(label)
    cover = None
    for relation in result.get('relationships') or []:
        if relation.get('type') == 'cover_art':
            fname = (relation.get('attributes') or {}).get('fileName')
            if fname:
                escaped = urllib.parse.quote(str(fname), safe='._-')
                cover = f'https://uploads.mangadex.org/covers/{manga_id}/{escaped}.512.jpg'
                break
    rating = attrs.get('contentRating', 'safe')
    # Capas sugestivas podem ser publicas; para erotica nao anexamos capa em canal geral.
    if rating not in ('safe', 'suggestive'):
        cover = None
    # Nunca copiar sinopses do MangaDex automaticamente: podem ser traducoes mecanicas.
    native_titles = []
    for container in [attrs.get('title') or {}, *(attrs.get('altTitles') or [])]:
        if isinstance(container, dict):
            native_titles.extend(str(v) for lang, v in container.items() if lang == 'ja' and isinstance(v, str))
    return {
        'id': manga_id,
        'titulo': title_from_map(attrs.get('title')),
        'sinopse': 'Sinopse em preparação — aguardando adaptação da fonte oficial japonesa.',
        'generos': tags[:9], 'classificacao': RATING_PT.get(rating, rating),
        'status': STATUS_PT.get(attrs.get('status'), 'Não informado'),
        'capa_url': cover,
        'links': {'mangadex': f'https://mangadex.org/title/{manga_id}'},
        'rating': rating, 'titulos_originais': native_titles, 'sinopse_revisada': False,
    }



def normalize_jp_title(value: object) -> str:
    """Normaliza pontuacao, espacos e caracteres de largura diferente."""
    normalized = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ''.join(char for char in normalized if char.isalnum())


def apply_curated_synopsis(work: dict, official_synopses: list[dict]) -> dict:
    """So usa adaptacoes explicitamente revisadas e ligadas a uma fonte oficial."""
    originals = {normalize_jp_title(t) for t in work.get('titulos_originais') or []}
    originals.discard('')
    matches = []
    for record in official_synopses:
        if not isinstance(record, dict) or record.get('revisada') is not True:
            continue
        title = normalize_jp_title(record.get('titulo_jp'))
        if title and title in originals:
            matches.append(record)
    if len(matches) > 1:
        print(f'AVISO: sinopses oficiais duplicadas para {work["id"]}; nenhuma aplicada.')
        return work
    if len(matches) == 1:
        record = matches[0]
        translated = clean(record.get('sinopse_ptbr'), 1800)
        source = safe_url(record.get('fonte_url'))
        if translated and source and source.startswith('https://'):
            work['sinopse'] = translated
            work['sinopse_fonte'] = source
            work['sinopse_revisada'] = True
            print(f'Sinopse oficial JP revisada aplicada: {work["id"]}.')
        else:
            print(f'AVISO: registro oficial incompleto para {work["id"]} (texto ou fonte).')
    return work


def add_manual_overrides(work: dict, override: dict) -> dict:
    for field in ('titulo', 'sinopse', 'sinopse_fonte', 'generos', 'classificacao', 'status', 'capa_url'):
        newval = override.get(field)
        if newval not in ('', None, []):
            if field == 'sinopse_fonte' and not safe_url(newval):
                print(f'AVISO: fonte de sinopse ignorada para {work["id"]}.')
                continue
            work[field] = newval
            if field == 'sinopse':
                work['sinopse_revisada'] = True
    links = dict(work.get('links') or {})
    for key, value in (override.get('links') or {}).items():
        if key in dict(LINK_LABELS):
            if value == '':
                links.pop(key, None)
            elif safe_url(value):
                links[key] = safe_url(value)
            else:
                print(f'AVISO: link {key} ignorado por nao ser HTTPS valido (obra {work["id"]}).')
    work['links'] = links
    return work


def compose_embed(work: dict) -> dict:
    title = md_text(work.get('titulo'), 220)
    synopsis = md_text(work.get('sinopse') or 'Sinopse em preparação.', 1700)
    genres = work.get('generos') or []
    if not isinstance(genres, list):
        genres = [str(genres)]
    genre_text = ', '.join(md_text(x, 65) for x in genres[:10]) or 'Não informado'
    status = md_text(work.get('status') or 'Não informado', 90)
    rating = md_text(work.get('classificacao') or 'Não informado', 90)
    links = work.get('links') or {}
    link_parts = []
    for key, label in LINK_LABELS:
        if safe_url(links.get(key)):
            link_parts.append(f'[{label}]({links[key]})')
    link_text = ' • '.join(link_parts) if link_parts else 'Links de leitura em preparação.'
    source_url = safe_url(work.get('sinopse_fonte'))
    source_note = f'\n\n📎 [Fonte original da sinopse (JP)]({source_url})' if source_url else ''
    description = (f'📜 **Sinopse**\n{synopsis}{source_note}\n\n'
                   f'🏷️ **Gêneros:** {genre_text}\n'
                   f'🟡 **Classificação:** {rating}\n'
                   f'📌 **Situação da obra:** {status}\n\n'
                   f'🔗 **Links de leitura:**\n{link_text}')
    embed = {
        'title': ('📖 ' + clean(work.get('titulo'), 245))[:256],
        'description': description[:4096], 'color': PINK,
        'footer': {'text': 'GIGA SCAN • Catálogo de obras • Tradução PT-BR'},
    }
    md_link = safe_url(links.get('mangadex'))
    if md_link:
        embed['url'] = md_link
    cover = safe_url(work.get('capa_url'))
    if cover:
        embed['image'] = {'url': cover}
    return embed


def make_payload(work: dict) -> dict:
    return {'username': 'GIGA SCAN • Catálogo',
            'allowed_mentions': {'parse': []},
            'embeds': [compose_embed(work)]}


def compute_hash(payload: dict) -> str:
    as_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(as_bytes).hexdigest()


def webhook_endpoint(webhook: str, message_id: str | None = None) -> str:
    # So dominio Discord, API oficial. Nao usar webhook arbitrario de terceiros.
    parsed = urllib.parse.urlsplit(webhook)
    if parsed.scheme != 'https' or parsed.netloc not in ('discord.com', 'discordapp.com'):
        raise RuntimeError('O webhook precisa ser do Discord (https://discord.com/api/webhooks/...).')
    if not re.fullmatch(r'/api/webhooks/\d+/[A-Za-z0-9._-]+/?', parsed.path):
        raise RuntimeError('Formato de webhook invalido. Use o webhook do canal de catalogo.')
    base = webhook.rstrip('/')
    if message_id:
        if not re.fullmatch(r'\d{10,30}', str(message_id)):
            raise RuntimeError('ID de mensagem invalido no estado do catalogo.')
        return f'{base}/messages/{message_id}'
    return base + '?wait=true'


def discord_publish(webhook: str, payload: dict, old_id: str | None) -> dict:
    if old_id:
        try:
            get_json(webhook_endpoint(webhook, old_id), payload=payload, method='PATCH')
            return {'id': str(old_id)}
        except RuntimeError as exc:
            if '404' not in str(exc):
                raise
            print('Mensagem anterior removida; criando ficha nova.')
    result = get_json(webhook_endpoint(webhook), payload=payload, method='POST')
    if not result.get('id'):
        raise RuntimeError('Discord nao retornou o ID da mensagem. Nao repetirei o envio automaticamente.')
    return result


def index_embeds(works: list[dict], state: dict) -> list[dict]:
    indexed = []
    for work in sorted(works, key=lambda w: clean(w.get('titulo')).casefold()):
        entry = state.get('messages', {}).get(work['id'], {})
        msg_id = entry.get('id')
        channel_id = state.get('channel_id')
        guild_id = state.get('guild_id')
        if not (msg_id and channel_id and guild_id):
            continue
        title = md_text(work['titulo'], 120)
        url = f'https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}'
        indexed.append(f'• [{title}]({url})')
    if not indexed:
        return []
    blocks = []
    current = []
    size = 0
    for entry in indexed:
        if size + len(entry) + 1 > 3300 and current:
            blocks.append('\n'.join(current))
            current = []
            size = 0
        current.append(entry)
        size += len(entry) + 1
    if current:
        blocks.append('\n'.join(current))
    if len(blocks) > 10:
        print('AVISO: catalogo muito grande; indice exibira apenas os primeiros 10 blocos.')
    embeds = []
    for i, block in enumerate(blocks[:10]):
        embeds.append({
            'title': '📚 ÍNDICE DE OBRAS | GIGA SCAN' if i == 0 else f'📚 Índice (parte {i+1})',
            'description': (('Clique no nome para abrir a ficha de cada mangá.\n\n' if i == 0 else '') + block),
            'color': PINK,
            'footer': {'text': f'GIGA SCAN • {len(indexed)} obras cadastradas • Fixe esta mensagem no canal' if i == 0 else 'GIGA SCAN • Continuação do índice'},
        })
    return embeds


def _safe_positive_int(value: object, default: int, maximum: int) -> int:
    try:
        return min(max(int(value), 1), maximum)
    except (ValueError, TypeError):
        return default


def main() -> int:
    settings = read_json(SETTINGS_FILE, {})
    group_id = settings.get('grupo_mangadex_id') or GROUP_DEFAULT
    if not re.fullmatch(r'[0-9a-f-]{36}', str(group_id)):
        raise RuntimeError('grupo_mangadex_id invalido.')
    state = read_json(STATE_FILE, {'messages': {}, 'index': {}})
    state.setdefault('messages', {})
    state.setdefault('index', {})
    preview = os.getenv('CATALOGO_PREVIA', '').lower() in ('1', 'true', 'yes')
    webhook = os.getenv('CATALOGO_DISCORD_WEBHOOK_URL', '').strip()
    if not preview:
        if not webhook:
            raise RuntimeError('Falta o segredo CATALOGO_DISCORD_WEBHOOK_URL no GitHub.')
        webhook_endpoint(webhook)  # Valida antes das chamadas.
    per_run = _safe_positive_int(os.getenv('CATALOGO_MAX_NOVAS', '60'), 60, 100)
    max_pages = _safe_positive_int(settings.get('max_paginas_mangadex', 15), 15, 50)
    include_erotica = settings.get('incluir_obras_eroticas', False) is True
    ids = fetch_group_manga_ids(group_id, max_pages)
    overrides = settings.get('ajustes_por_mangadex_id') or {}
    official_synopses = settings.get('sinopses_oficiais') or []
    works = []
    if not ids:
        print('AVISO: nenhum mangá foi localizado para a scan via API; nao enviaremos catalogo vazio.')
    for idx, manga_id in enumerate(ids, 1):
        try:
            work = fetch_manga(manga_id)
            if work['rating'] == 'pornographic' or (work['rating'] == 'erotica' and not include_erotica):
                print(f'Obra adulta omitida do canal geral: {manga_id}.')
                continue
            work = apply_curated_synopsis(work, official_synopses)
            work = add_manual_overrides(work, overrides.get(manga_id) or {})
            works.append(work)
            print(f'Obra {idx}/{len(ids)}: {work["titulo"]}.')
        except Exception as exc:
            print(f'AVISO: falha ao buscar uma obra ({manga_id}): {exc}')
        time.sleep(0.3)
    for extra in settings.get('obras_extras') or []:
        if not isinstance(extra, dict) or not extra.get('id') or not extra.get('titulo'):
            print('Obra manual incompleta ignorada (precisa de id e titulo).')
            continue
        manual = {
            'id': 'extra:' + str(extra['id']),
            'titulo': extra['titulo'], 'sinopse': extra.get('sinopse') or 'Sinopse em preparação.',
            'generos': extra.get('generos') or [], 'classificacao': extra.get('classificacao') or 'Não informada',
            'status': extra.get('status') or 'Não informado', 'capa_url': extra.get('capa_url'),
            'links': {k: v for k, v in (extra.get('links') or {}).items()
                      if k in dict(LINK_LABELS) and safe_url(v)}, 'rating': 'manual',
            'sinopse_fonte': safe_url(extra.get('sinopse_fonte')),
        }
        works.append(manual)
    print(f'Catalogo montado: {len(works)} obras (MangaDex + manuais).')
    print(f'Sinopses originais revisadas: {sum(bool(w.get("sinopse_revisada")) for w in works)} de {len(works)}. Restantes ficam em preparacao.')
    if preview:
        for work in works:
            print(f'- {work["titulo"]} | {work["id"]} | {len(work["links"])} link(s) | Sinopse: {"revisada" if work.get("sinopse_revisada") else "em preparacao"}')
        print('PREVIA: nenhuma mensagem enviada ao Discord e nenhum arquivo modificado.')
        return 0
    if not works:
        raise RuntimeError('Nenhuma obra a publicar. Confira o ID do grupo ou as opcoes do catalogo.')
    published = updated = skipped = failed = 0
    for work in sorted(works, key=lambda w: clean(w.get('titulo')).casefold()):
        entry = state['messages'].get(work['id'], {})
        payload = make_payload(work)
        digest = compute_hash(payload)
        if entry.get('id') and entry.get('hash') == digest:
            skipped += 1
            continue
        if not entry.get('id') and published >= per_run:
            print(f'Limite de {per_run} fichas novas por execucao; restante para proxima.');
            continue
        try:
            result = discord_publish(webhook, payload, entry.get('id'))
            state['messages'][work['id']] = {'id': result['id'], 'hash': digest}
            if result.get('guild_id') and result.get('channel_id'):
                state['guild_id'] = str(result['guild_id'])
                state['channel_id'] = str(result['channel_id'])
            save_json(STATE_FILE, state)
            if entry.get('id') and result['id'] == entry['id']:
                updated += 1
                print(f'EDITADA: {work["titulo"]}.')
            else:
                published += 1
                print(f'PUBLICADA: {work["titulo"]}.')
            time.sleep(1.2)
        except Exception as exc:
            failed += 1
            print(f'ERRO NA FICHA {work["id"]}: {exc}')
    embeds = index_embeds(works, state)
    if embeds:
        index_payload = {'username': 'GIGA SCAN • Catálogo', 'allowed_mentions': {'parse': []},
                         'content': '📚 **Índice oficial das obras da GIGA SCAN**',
                         'embeds': embeds}
        digest = compute_hash(index_payload)
        entry = state.get('index') or {}
        if entry.get('hash') != digest or not entry.get('id'):
            try:
                result = discord_publish(webhook, index_payload, entry.get('id'))
                state['index'] = {'id': result['id'], 'hash': digest}
                save_json(STATE_FILE, state)
                print('Indice enviado/atualizado. Fixe a mensagem do indice no Discord.')
            except Exception as exc:
                failed += 1
                print(f'ERRO NO INDICE: {exc}')
    print(f'RESUMO: {published} criadas, {updated} editadas, {skipped} inalteradas, {failed} falhas.')
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'ERRO FATAL: {exc}', file=sys.stderr)
        sys.exit(1)
