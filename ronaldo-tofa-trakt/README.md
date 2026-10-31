# Tofa para Trakt

App pessoal para umbrelOS 2.0: mostra **assistindo agora** no Trakt e registra o histórico concluído do Tofa. Código e imagem estão versionados nesta loja; as autorizações ficam somente nos dados do app.

## Instalar ou reinstalar

1. Adicione esta loja ao Umbrel: `https://github.com/RONALDOAMD9/ronaldo-umbrel-apps`.
2. Instale **Tofa para Trakt**, junto com o Tofa da mesma loja.
3. Abra o aplicativo. O endereço interno já aponta para o Tofa deste Umbrel.
4. Clique em **Conectar Tofa** e depois **Conectar Trakt**. Abra os links, informe os códigos e autorize suas contas.
5. Inicie uma reprodução no Tofa. O painel informa quando os eventos estão conectados.

A interface é protegida pelo login do Umbrel através de `app_proxy`. O servidor do app não publica uma porta independente. A porta da interface no Umbrel é **3431**.

O Client ID padrão pertence ao aplicativo pessoal do Ronaldo, que aceita sua própria conta Trakt. Outra pessoa deve criar seu próprio aplicativo em https://developer.trakt.tv/apps e informar o Client ID no painel. Nenhum Client Secret é necessário no fluxo de dispositivo atual.

## Migração da ponte avulsa anterior

Ao iniciar ou atualizar pelo Umbrel, um hook identifica somente o contêiner `tofa-trakt-sync-1` do projeto `tofa-trakt`, confirma a origem da pasta de dados e o desliga. Importa as autorizações e o controle de envios, preserva os originais e salva um backup privado em `data/legacy-backup.tar.gz`. A migração ocorre uma única vez e não substitui contas já conectadas no novo app. Não é necessário informar a senha do terminal para essa migração pelo Umbrel.

## Antes de formatar

Clique em **Baixar backup** no painel e guarde o `.tar.gz` fora do Umbrel. Ele inclui:

- Configuração e data inicial do histórico.
- Autorizações OAuth do Tofa e do Trakt.
- Banco SQLite com o controle de registros enviados e pendentes.

**Este backup é privado e contém credenciais. Nunca envie o arquivo ao GitHub.** Depois de reinstalar pela loja, use **Restaurar um backup**. Se as autorizações tiverem expirado ou sido revogadas, conecte as contas novamente.

GitHub guarda a solução reinstalável; não guarda seus tokens. Sem backup, você ainda consegue reinstalar e vincular novamente. A data inicial padrão é o instante da instalação, para não reenviar histórico antigo involuntariamente.

O diretório persistente é `${APP_DATA_DIR}/data`. Um backup nativo do app pelo Umbrel também deve preservar esse diretório. Formatar sem salvar os dados apaga as autorizações e o controle local de envios.

## Como funciona

- Abre `/api/v1/ws` no Tofa e autentica com um token de dispositivo, com reconexão automática.
- Usa `StreamStarted`, `StreamProgress` e `StreamStopped` para informar início, retomada, mudança de posição e término ao Trakt.
- Envia `/scrobble/start` com o ID exato do filme/episódio e a porcentagem. O Trakt calcula sua própria previsão de término pela duração cadastrada lá.
- Ao pausar ou encerrar, usa `/scrobble/pause` para limpar o estado ao vivo. A gravação definitiva tem um único responsável: o sincronizador de histórico, que lê `end_reason=completed` do Tofa a cada minuto. Isso evita que o scrobble e o histórico gravem o mesmo episódio duas vezes ou usem limites de conclusão diferentes.
- O Tofa não fornece um evento explícito de pausa. A ponte a infere após 75 segundos sem avanço e retoma quando a posição avança.
- Segue apenas o usuário autenticado. É possível restringir também pelo ID do perfil Tofa. Uma conta Trakt mantém um estado “assistindo” por vez; a reprodução mais recentemente iniciada da conta prevalece.
- Após um reinício durante uma reprodução, tenta recuperar a sessão pelo snapshot e pelo histórico aberto. Se o Tofa não conseguir identificar o arquivo nessa recuperação, um novo início de reprodução fornece os dados necessários.
- A previsão no painel usa a duração do arquivo; a do Trakt usa os metadados do Trakt. Edições diferentes podem ter previsões diferentes.

## Duplicados, segurança e limites

Antes de enviar um concluído, verifica a identidade do título/episódio e o minuto de conclusão no Trakt. Um registro confirmado fica em SQLite. Timeouts ambíguos permanecem pendentes, sem reenvio automático. Não exclua `state.sqlite`.

Marcações manuais de “assistido” que não criem sessão de reprodução não são sincronizadas. Arquivos sem identificador confiável ou episódio identificado ficam sem envio até que os metadados sejam corrigidos no Tofa. Outros usuários do servidor nunca são enviados para a conta vinculada.

Tokens são salvos com permissão 600, nunca aparecem na API de estado ou no repositório. A interface exige proteção contra CSRF nas operações de configuração, conexão e backup. O contêiner não acessa o socket Docker nem os arquivos de mídia e executa sem privilégios após preparar somente sua pasta de dados.

## Desenvolvimento

```sh
python -m venv .venv
.venv/bin/pip install -r app/requirements.txt
.venv/bin/python -m unittest discover -s app -p 'test_*.py'
DATA_DIR=/tmp/tofa-trakt-dev BIND_HOST=127.0.0.1 PORT=8787 .venv/bin/python app/app.py
```

A workflow `.github/workflows/tofa-trakt.yml` testa e publica a imagem em GHCR para amd64 e arm64. O pacote Umbrel é `1.0.3`, com a imagem de execução `1.0.2` fixada por digest; atualizações devem incrementar a versão da imagem e do manifesto juntas.

## Referências

- https://docs.tofa.tv/live-events-api.html
- https://docs.tofa.tv/use-the-api.html
- https://developer.trakt.tv/
- https://github.com/getumbrel/umbrel-community-app-store

Projeto independente, sem afiliação ou endosso de Tofa ou Trakt.
