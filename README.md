# Conexão DP V3 Python — demonstração pública

Esta distribuição é destinada a estudo e demonstração local. Ela não inclui dados reais. O banco é criado vazio; use somente registros fictícios.

## Requisitos

- Python 3.12+
- Node.js e pnpm para a interface web

## Iniciar (Windows PowerShell)

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force dados | Out-Null
python -m conexaodp.cli --db dados/demo.sqlite3 init
python -m conexaodp.cli --db dados/demo.sqlite3 admin
python -m conexaodp.cli --db dados/demo.sqlite3 serve
```

Crie a conta usando um nome fictício e endereço reservado como `demo@example.com`, com senha exclusiva. Em outro terminal:

```powershell
pnpm install
pnpm dev:next
```

Abra `http://localhost:8794`; o backend atende em `http://127.0.0.1:8792`.

## Demonstração sintética

Cadastre manualmente pessoas como “Pessoa Exemplo 01” e empresas como “Empresa Demonstração A”. Não importe planilhas, backups nem snapshots de instalações reais. Use apenas endereços `example.com`.

Integrações externas ficam desativadas. Não configure SMTP, Clicksign, Z-PRO, Selecty ou VoxFy com credenciais reais. `.env.example` só tem valores vazios e `.env` é ignorado pelo Git.

## Conteúdo público

A pasta `dados/`, bancos, backups, logs, documentos, exports, capturas e histórico Git da pasta original não fazem parte desta cópia. Revise arquivos antes de publicar e nunca inclua dados de RH ou credenciais.
