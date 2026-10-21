# ScreenCam - tela do PC como câmera ONVIF no DVR

Publica a tela do PC (ex.: PDV) como uma câmera IP. O DVR cadastra o PC por **ONVIF** e grava como qualquer canal.

## Opção A (recomendada): instalador "próximo, próximo, concluir"
**Gerar o instalador (uma vez, em qualquer PC Windows com internet):**
1. Dê dois cliques em `GERAR_INSTALADOR.bat`. Ele baixa tudo e cria `installer\Output\ScreenCam_Setup.exe`.

**Instalar em cada PC (sem digitar comandos):**
1. Copie o `ScreenCam_Setup.exe` e dê dois cliques.
2. Informe o nome da câmera, o usuário e a senha e clique em Avançar até concluir.
   O instalador já inclui tudo (Python, MediaMTX, FFmpeg), libera o firewall, cria o início automático e abre o painel.
3. Cadastre no DVR (veja abaixo).
*Obs.: por não ter assinatura digital, o Windows pode mostrar "SmartScreen": clique em Mais informações > Executar assim mesmo.*

## Opção B: scripts manuais
`install.ps1` (precisa do Python instalado). Detalhes no final do arquivo.

## Cadastrar no DVR (Intelbras)
*Configurações > Câmera > Disp. Remoto > Adicionar*:
- Protocolo: **Onvif** | IP: IP do PC | Porta RTSP: 554 | Porta HTTP: 8080
- Usuário / senha: os do painel | Canais: 1 | Canal remoto: 1
Teste antes no VLC: `rtsp://admin:SENHA@IP_DO_PC:554/live`

## Deixar 100% automático
- **Login automático** no Windows (`netplwiz`): a captura só funciona com a sessão do usuário aberta.
- Desative bloqueio de tela e suspensão (tela bloqueada = imagem preta). Evite fechar sessão por Área de Trabalho Remota (use o AnyDesk/TeamViewer ou o painel web).
- IP fixo no PC. Se o Windows pedir permissão de rede ao iniciar, escolha "Rede privada".

## Problemas comuns
- *DVR diz "falha de login"*: confira usuário/senha e se o relógio do PC está certo.
- *Imagem preta*: sessão bloqueada/desconectada.
- *Imagem cortada com escala 125%/150%*: troque "Tela" para "Todos os monitores" no painel.
- *Logs*: `C:\ScreenCam\screencam.log`, `ffmpeg.log`, `mediamtx.log`.

## Remover
Configurações do Windows > Aplicativos > ScreenCam > Desinstalar (ou `uninstall.ps1` na opção B).
