# Ronaldo App Store

Loja pessoal de aplicativos para umbrelOS 2.0.

## Tofa

- Imagem oficial: `ghcr.io/tofatv/tofa:beta` (tag mutável).
- Interface: porta `33333`, rede host conforme documentação do Tofa.
- Dados persistentes: `${APP_DATA_DIR}/data` → `/data`.
- Biblioteca: `Home/Videos` (`${UMBREL_ROOT}/home/Videos`) → `/media`, somente leitura.

Adicione https://github.com/RONALDOAMD9/ronaldo-umbrel-apps em App Store → Community App Stores e instale Tofa.
Depois abra o aplicativo e vincule o servidor à sua conta Tofa. Ao adicionar a biblioteca, selecione `/media`.

O ícone é uma ilustração própria deste pacote, não o logotipo oficial do Tofa.

Referências: https://tofa.tv/install e https://github.com/getumbrel/umbrel-community-app-store
