# GIGA SCAN — Anúncios automáticos no Discord

Monitora capítulos em português publicados pela **GIGA SCAN** no MangaDex e anuncia no canal **#lançamentos** usando um webhook do Discord.

Não precisa deixar o computador ligado nem instalar programas. O GitHub Actions executa o script aproximadamente a cada 30 minutos (podem ocorrer atrasos).

## Instalação sem programar

1. Extraia o conteúdo do ZIP recebido. Ele contém `bot.py`, `state.json`, `README.md` e a pasta `.github`.
2. No seu repositório vazio do GitHub, clique em **uploading an existing file** (ou em **Add file → Upload files**).
3. Arraste **todos os arquivos e a pasta `.github`** de dentro da pasta extraída. Confirme que o GitHub receberá `.github/workflows/anunciar-capitulos.yml`.
4. Clique em **Commit changes** para finalizar o envio.
5. No Discord: **#lançamentos → Editar canal → Integrações → Webhooks → Novo webhook**. Copie a URL do webhook. **Não a envie a ninguém e não coloque em arquivos públicos.**
6. No GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Nome: `DISCORD_WEBHOOK_URL`. Valor: cole a URL secreta do webhook. Salve.
7. Em **Settings → Actions → General**, confira que o GitHub Actions está habilitado. Se a gravação de `state.json` falhar por falta de permissão, ajuste **Workflow permissions** para **Read and write permissions** e salve.
8. Acesse a aba **Actions → Anunciar capítulos da GIGA SCAN → Run workflow**, marque **teste** e execute. Se tudo estiver correto, aparecerá no Discord uma mensagem de teste.
9. Execute **Run workflow** novamente, agora **sem marcar teste**. Esta execução registra os capítulos já existentes e **não envia anúncios antigos**.
10. A partir daí, capítulos realmente novos do grupo no MangaDex serão anunciados automaticamente durante as verificações.

## Observações importantes

- Apenas capítulos associados ao grupo MangaDex `c26db585-798d-4da5-9d0f-8a0ffd141f50` são considerados.
- A automação cobre **somente o MangaDex**, não Sakura, Kuro, Capivara ou site próprio.
- O bot não inclui capas automaticamente, porque o canal é geral e algumas capas podem conter conteúdo inadequado. Podemos adicionar capas seguras mais tarde.
- A API é consultada apenas para capítulos publicados em `pt-br`, e para classificações `safe`, `suggestive` e `erotica`; exclui classificações explicitamente pornográficas.
- O histórico das últimas publicações fica em `state.json`, atualizado por commits do GitHub Actions. Não edite manualmente esse arquivo.
- Se a automação enviar o anúncio, mas a etapa de salvar o histórico falhar, existe chance de anúncio duplicado na execução seguinte. Verifique o log do Actions.
- O agendamento do GitHub Actions pode atrasar. Em repositórios públicos, ele pode ser desativado após 60 dias sem atividade, exigindo reativação manual.
- Evite mudar o repositório para privado sem rever os limites do plano GitHub.

## Como testar se deu erro

Abra **Actions → execução com erro → Consultar MangaDex ou testar Discord**. O log mostrará o status de erro, sem expor a URL do webhook.

Código com biblioteca padrão Python: não há `pip install` nem necessidade de rodar um servidor próprio.
