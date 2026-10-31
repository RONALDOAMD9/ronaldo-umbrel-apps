# Expo App Store

Loja pessoal de aplicativos para umbrelOS 2.0.

## Tofa

- Imagem oficial: `ghcr.io/tofatv/tofa:beta` (tag mutável).
- GPU Intel integrada do Core i5-12400: `/dev/dri` disponibilizado ao container para transcodificação por hardware.
- Interface: porta `33333`, rede host conforme documentação do Tofa.
- Dados persistentes: `${APP_DATA_DIR}/data` → `/data`.
- Biblioteca: `Home/Videos` (`${UMBREL_ROOT}/home/Videos`) → `/media`, somente leitura.

Adicione https://github.com/RONALDOAMD9/ronaldo-umbrel-apps em App Store → Community App Stores e instale Tofa.
Depois abra o aplicativo e vincule o servidor à sua conta Tofa. Ao adicionar a biblioteca, selecione `/media`.

Ícone oficial do Tofa: https://app.tofa.tv/logo.svg

Referências: https://tofa.tv/install e https://github.com/getumbrel/umbrel-community-app-store

## Tofa para Trakt

Instale **Tofa para Trakt** nesta mesma loja para mostrar a reprodução em tempo real no Trakt e sincronizar os concluídos do Tofa. Abra o app na porta 3431 e vincule suas duas contas por código.

Inclui backup privado para restaurar as autorizações e o controle de envios depois de uma formatação. O GitHub mantém o app reinstalável; seus tokens ficam nos dados do Umbrel. Veja [instalação, backup e funcionamento](ronaldo-tofa-trakt/README.md).
