# DockPeek para umbrelOS 2.0

Aplicativo da Expo App Store. Instale pela loja comunitária já adicionada ao Umbrel.

Login: `umbrel`. Use a senha gerada que o Umbrel apresenta para este aplicativo; ela não é a senha da sua conta Umbrel.

Imagem oficial: `dockpeek/dockpeek:latest`. Interface interna: porta 8000, acessada pelo app_proxy. SECRET_KEY usa APP_SEED; PASSWORD usa APP_PASSWORD, fornecidos pelo Umbrel.

O socket Docker é montado somente no socket proxy privado, sem publicar a porta 2375 na rede local. DockPeek pode consultar containers, imagens, redes e logs, além de atualizar containers. Atualize os aplicativos gerenciados pelo Umbrel preferencialmente pela própria App Store para manter suas versões e configurações coerentes. A montagem :ro do socket não impede alterações pela API.

Não há volume persistente exigido pela configuração oficial básica do DockPeek. Não são necessários host network, privileged ou acesso à biblioteca de mídia.

As imagens usam tags latest mutáveis. A versão 1.0.0 do manifesto identifica este pacote, não a versão upstream. Arquivos YAML e ZIP foram validados localmente; a instalação não foi testada em um Umbrel real.

Fontes: https://github.com/dockpeek/dockpeek e https://github.com/getumbrel/umbrel-community-app-store
