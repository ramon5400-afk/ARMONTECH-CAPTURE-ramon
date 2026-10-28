# ScreenCam

Transforma a tela de um PC (ex.: PDV) em uma **câmera IP ONVIF** que o DVR Intelbras encontra pelo botão **Buscar** e grava como qualquer canal.

## Estrutura
| Pasta / arquivo | Função |
|---|---|
| `src/screencam.py` | O programa (servidor ONVIF, descoberta automática, RTSP, captura, painel web, watchdog) |
| `installer/` | Instalador (Inno Setup): `ScreenCam.iss`, `postinstall.ps1`, `preuninstall.ps1` |
| `build.ps1` / `GERAR_INSTALADOR.bat` | Geram o `ScreenCam_Setup.exe` (baixam Python, MediaMTX, FFmpeg e compilam) |
| `.github/workflows/build.yml` | Gera o instalador automaticamente no GitHub |
| `tools/` | Scripts manuais (instalar/remover sem o instalador) |

## Gerar o instalador pelo GitHub (recomendado)
1. Aba **Actions** > **Gerar instalador** > **Run workflow** (também roda sozinho a cada commit).
2. Quando ficar verde, abra a execução mais recente e baixe **ScreenCam_Setup_execucaoN** em *Artifacts*.
3. Dentro do zip está o `ScreenCam_Setup.exe`. Ao criar uma tag `v*`, o `.exe` também vai para **Releases**.

Para gerar no próprio PC: dois cliques em `GERAR_INSTALADOR.bat` (precisa de internet).

> **Versão:** fica em `src/screencam.py` (`VERSION`) e em `installer/ScreenCam.iss` (`AppVer`). O gerador **para com erro** se forem diferentes. Ao mudar uma, mude a outra.

## Instalar em cada PC
1. Dois cliques no `ScreenCam_Setup.exe`.
2. Informe nome da câmera, usuário e senha > Avançar até concluir.
3. O painel abre em `http://IP_DO_PC:8090` (mostra a versão ao lado do título).

O instalador já inclui tudo, libera o firewall, inicia com o Windows e, ao reinstalar por cima, mantém as configurações. Para o início automático funcionar, ative o **login automático** do Windows (`netplwiz`) e desative o bloqueio de tela (tela bloqueada = imagem preta).

## Cadastrar no DVR
*Configurações > Câmera > Disp. Remoto* > **Buscar** > marque o PC > **Adicionar**.
Se o DVR pedir login, use o usuário/senha do ScreenCam (ou cadastre-os em **Usuário/Senha Padrão** do DVR).
Cadastro manual: protocolo **Onvif**, IP do PC, porta HTTP **8080**, porta RTSP **554**.

## Diagnóstico
- Painel > **Testar captura**: mostra a imagem que o DVR recebe.
- Painel > **Analisar vídeo**: mostra como o vídeo é montado (perfil, quadros-chave, SPS/PPS, fatias). Cole o endereço RTSP de outro fluxo (ex.: o MA Capture) para comparar.
- Painel > **Modo do vídeo**: *Compatível* gera o vídeo no formato clássico de câmera IP (1 fatia, AUD, SPS/PPS a cada quadro-chave) para DVRs exigentes.
- Painel > **Log do servidor de vídeo**: mostra as conexões do DVR e falhas de senha.
- VLC: `rtsp://usuario:senha@IP_DO_PC:554/live`.
- Aviso vermelho "A captura perdeu o acesso à tela (erro 5)": a tela está bloqueada, com protetor de tela, em sessão remota desconectada ou com janela de permissão do Windows. Desative o bloqueio/protetor; se persistir, teste **Método de captura: DXGI (experimental)** no painel.
- Arquivos em `C:\ScreenCam`: `screencam.log`, `ffmpeg.log`, `mediamtx.log`.
- PC não aparece no Buscar: mesma rede do DVR e reinstalar pelo instalador (libera a porta UDP 3702).

## Portas
554 (RTSP), 8080 (ONVIF), 8090 (painel), 3702/UDP (descoberta), 8000-8001/UDP (RTP).
