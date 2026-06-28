# Comandos principais

Comandos para executar a aquisicao local de playback. Rode tudo a partir da
raiz do repositorio:

```powershell
Set-Location C:\dev\asvspoof5
```

## Ambiente

Permitir scripts PowerShell no usuario atual:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Criar o ambiente, se ainda nao existir:

```powershell
py -3.11 -m venv .venv-capture
```

Ativar o ambiente:

```powershell
.\.venv-capture\Scripts\Activate.ps1
```

Instalar dependencias de captura:

```powershell
python -m pip install -r requirements-capture.txt
```

## Dispositivos

Listar entradas e saidas de audio vistas pelo PortAudio:

```powershell
python scripts/capture_test.py --list-devices
```

Prefira configurar por nome de dispositivo e host API quando possivel. Indices
podem mudar depois de reconectar USB ou reiniciar o Windows.

## Configuracao por condicao

Copiar o exemplo antes de uma sessao real:

```powershell
Copy-Item config\acquisition.example.json acquisition-config.HH.json
```

Troque `HH` por `HL`, `LH` ou `LL` conforme a condicao. O campo `"condition"`
dentro do JSON precisa bater com `--condition`.
Mantenha `"execution.maximum_attempts_per_job": 1`; novas tentativas sao feitas
em outra passagem, nao imediatamente no mesmo audio.

Condicoes:

| Condicao | Playback | Gravacao |
| --- | --- | --- |
| `HH` | alta qualidade | alta qualidade |
| `HL` | alta qualidade | baixa qualidade |
| `LH` | baixa qualidade | alta qualidade |
| `LL` | baixa qualidade | baixa qualidade |

## Piloto descartavel

Gerar apenas o plano do piloto, sem abrir dispositivos:

```powershell
python scripts/capture_test.py --condition HH --count 12 --plan-only --clean
```

Rodar piloto usando um arquivo de configuracao aprovado:

```powershell
python scripts/capture_test.py `
  --condition HH `
  --count 12 `
  --config acquisition-config.HH.json `
  --clean
```

Rodar piloto informando dispositivos na linha de comando e salvando o JSON:

```powershell
python scripts/capture_test.py `
  --condition HL `
  --input-device 18 `
  --output-device 15 `
  --input-host-api "Windows WASAPI" `
  --output-host-api "Windows WASAPI" `
  --recording-equipment "Audio-Technica AT2020" `
  --playback-equipment "Yamaha HS5" `
  --audio-interface "modelo/serial da interface" `
  --distance "1.0 m" `
  --speaker-volume "posicao fixa do knob" `
  --microphone-gain "posicao fixa do ganho" `
  --windows-audio-enhancements-disabled `
  --save-config acquisition-config.HL.json `
  --count 12 `
  --clean
```

Cada tentativa de captura imprime uma linha `SUCCESS` ou `FAILED` com o `job`,
particao, categoria, fonte, tentativa e diagnostico resumido.
O script faz uma tentativa por audio em cada execucao; se falhar, registra a
falha e passa para o proximo.

## Limpeza de pilotos

Limpar somente uma condicao de teste e sair:

```powershell
python scripts/capture_test.py --condition LL --clean --clean-only
```

Limpar todos os pilotos locais em `capture-tests/` e sair:

```powershell
python scripts/capture_test.py --clean-all
```

Limpar todos os pilotos e iniciar um novo piloto para uma condicao:

```powershell
python scripts/capture_test.py --condition HH --clean-all --count 12 --plan-only
```

## Execucao definitiva

Verificar o plano e o arquivo de configuracao sem abrir dispositivos:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json `
  --dry-run
```

Resolver dispositivos e confirmar a cadeia fisica sem gravar jobs:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json `
  --preflight-only
```

Executar a gravacao definitiva da condicao:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json
```

Retentar jobs falhos depois de corrigir a cadeia fisica. Cada job falho recebe
uma nova tentativa nessa passagem:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json `
  --retry-failed
```

## Plano de captura

Regerar e validar o plano deterministico, quando necessario:

```powershell
node scripts/generate-capture-plan.mjs
```

## Artefatos locais

- Pilotos: `capture-tests/<condition>/`
- Saidas definitivas: `playback_flac/<partition>/<condition>/<PH|PS>/`
- Ledger definitivo: `capture-ledgers/<condition>/capture-ledger.sqlite3`
- Falhas definitivas: `capture-failures/<condition>/`
