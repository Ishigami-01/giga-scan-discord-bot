"""Anuncia os capítulos novos da GIGA SCAN no Discord via MangaDex.

Sem bibliotecas externas: roda no GitHub Actions usando Python padrão.
O primeiro uso cria uma referência de capítulos existentes, sem postar o histórico.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GROUP_ID = "c26db585-798d-4da5-9d0f-8a0ffd141f50"
API_BASE = "https://api.mangadex.org"
STATE_FILE = Path(__file__).with_name("state.json")
MAX_CHAPTERS_PER_CHECK = 100
MAX_ANNOUNCEMENTS_PER_RUN = 10
MAX_REMEMBERED_IDS = 500
EMBED_COLOR = 0xFF79B0
USER_AGENT = "GigaScan-Discord-Notifier/1.0 (personal GitHub Actions notifier)"


def timestamp_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_json(url, *, data=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    method = "GET"
    if data is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content.decode("utf-8")) if content else None
        except urllib.error.HTTPError as exc:
            # Nao imprimir o URL: ele pode conter o token secreto do webhook.
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                retry_after = exc.headers.get("Retry-After", "")
                try:
                    delay = min(max(float(retry_after), 1), 30)
                except (TypeError, ValueError):
                    delay = 3 * (attempt + 1)
                time.sleep(delay)
                continue
            raise RuntimeError(f"Solicitação HTTP falhou (status {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"Falha de conexão ({type(exc).__name__}).") from None
    raise RuntimeError("Limite de tentativas atingido.")


def fetch_chapters():
    params = [
        ("limit", str(MAX_CHAPTERS_PER_CHECK)),
        ("groups[]", GROUP_ID),
        ("translatedLanguage[]", "pt-br"),
        ("order[publishAt]", "desc"),
        ("includes[]", "manga"),
        # Mantém o conteúdo explicitamente pornográfico fora de anúncios gerais.
        ("contentRating[]", "safe"),
        ("contentRating[]", "suggestive"),
        ("contentRating[]", "erotica"),
    ]
    query = urllib.parse.urlencode(params)
    response = request_json(f"{API_BASE}/chapter?{query}")
    if not isinstance(response, dict) or response.get("result") != "ok":
        raise RuntimeError("Resposta inesperada da API do MangaDex.")
    chapters = response.get("data")
    if not isinstance(chapters, list):
        raise RuntimeError("Lista de capítulos ausente na resposta do MangaDex.")
    # Validar a relação do grupo para evitar anúncios de outras scans.
    verified = []
    for chapter in chapters:
        if not isinstance(chapter, dict) or not chapter.get("id"):
            continue
        groups = [r.get("id") for r in chapter.get("relationships", [])
                  if r.get("type") == "scanlation_group"]
        if GROUP_ID in groups:
            verified.append(chapter)
        else:
            print(f"Ignorando capítulo sem relação confirmada com a GIGA SCAN: {chapter['id']}")
    return verified


def manga_from_chapter(chapter):
    for relation in chapter.get("relationships", []):
        if relation.get("type") == "manga":
            return relation
    return None


def choose_title(title_map):
    if not isinstance(title_map, dict):
        return None
    for language in ("pt-br", "en", "ja-ro", "ja"):
        value = title_map.get(language)
        if value:
            return str(value)
    return str(next(iter(title_map.values()))) if title_map else None


def manga_details(chapter):
    relation = manga_from_chapter(chapter)
    if not relation:
        return "Mangá da GIGA SCAN", None
    manga_id = relation.get("id")
    attributes = relation.get("attributes") or {}
    if not attributes and manga_id:
        # Fallback para casos em que o API não expande o título no /chapter.
        response = request_json(f"{API_BASE}/manga/{manga_id}")
        attributes = ((response or {}).get("data") or {}).get("attributes") or {}
    title = choose_title(attributes.get("title")) or "Mangá da GIGA SCAN"
    return title[:240], manga_id


def make_announcement(chapter):
    attrs = chapter.get("attributes") or {}
    title, manga_id = manga_details(chapter)
    chapter_id = chapter["id"]
    number = attrs.get("chapter")
    chapter_label = f"Capítulo {number}" if number else "Novo capítulo"
    extra_name = attrs.get("title")
    line = f"**{chapter_label}**"
    if extra_name and str(extra_name).strip():
        line += f" — {str(extra_name).strip()[:120]}"
    url = f"https://mangadex.org/chapter/{chapter_id}"
    embed = {
        "title": f"📚 {title}"[:256],
        "url": url,
        "description": (
            f"🎉 **Novo lançamento da GIGA SCAN!**\n\n"
            f"{line}\n\n"
            f"📖 [Ler o capítulo no MangaDex]({url})"
        )[:4096],
        "color": EMBED_COLOR,
        "footer": {"text": "GIGA SCAN • Tradução PT-BR • Fonte: MangaDex"},
    }
    published = attrs.get("publishAt")
    if isinstance(published, str) and published:
        embed["timestamp"] = published
    # A capa não é acrescentada automaticamente para evitar imagens impróprias
    # em canais gerais; poderá ser habilitada após revisão editorial.
    return {
        "username": "GIGA SCAN • Lançamentos",
        "content": "📢 **Novo capítulo disponível!**",
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }


def notify(webhook_url, payload):
    request_json(webhook_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "seen_ids": []}
    with STATE_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict) or not isinstance(state.get("seen_ids", []), list):
        raise RuntimeError("state.json inválido; não altere manualmente esse arquivo.")
    return state


def save_state(state):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(STATE_FILE)


def main():
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith(
        "https://discordapp.com/api/webhooks/"
    ):
        raise RuntimeError("Configure o segredo DISCORD_WEBHOOK_URL no GitHub antes de executar.")

    if os.getenv("TEST_ONLY", "").strip().lower() == "true":
        notify(webhook, {
            "username": "GIGA SCAN • Lançamentos",
            "content": "✅ **Teste da automação GIGA SCAN:** webhook funcionando!",
            "allowed_mentions": {"parse": []},
        })
        print("Mensagem de teste enviada ao Discord.")
        return

    chapters = fetch_chapters()
    print(f"Capítulos recentes confirmados da GIGA SCAN: {len(chapters)}")
    state = load_state()
    seen = list(dict.fromkeys(str(x) for x in state.get("seen_ids", [])))

    if not state.get("initialized", False):
        # Primeira execução: guardar o histórico sem anunciá-lo.
        state["initialized"] = True
        state["seen_ids"] = list(dict.fromkeys(ch["id"] for ch in chapters))[-MAX_REMEMBERED_IDS:]
        state["initialized_at"] = timestamp_now()
        save_state(state)
        print("Primeira execução concluída: histórico inicial registrado SEM enviar anúncios.")
        return

    unseen = [chapter for chapter in reversed(chapters) if chapter["id"] not in set(seen)]
    if not unseen:
        print("Nenhum capítulo novo; nada a publicar.")
        return

    print(f"Capítulos ainda não anunciados: {len(unseen)}")
    for chapter in unseen[:MAX_ANNOUNCEMENTS_PER_RUN]:
        payload = make_announcement(chapter)
        notify(webhook, payload)
        print(f"Anúncio publicado: capítulo ID {chapter['id']}")
        seen.append(chapter["id"])
        state["seen_ids"] = seen[-MAX_REMEMBERED_IDS:]
        state["last_announced_at"] = timestamp_now()
        save_state(state)
    if len(unseen) > MAX_ANNOUNCEMENTS_PER_RUN:
        print("Outros capítulos novos serão enviados na próxima execução.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)
