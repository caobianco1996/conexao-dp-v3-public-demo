# Conexão DP V3 Python — demonstração pública

Esta distribuição é destinada a estudo e demonstração local. Ela não inclui dados reais. O banco é criado vazio; use somente registros fictícios.

## Funcionalidades

| Área | O que o programa oferece |
| --- | --- |
| Início e operações de DP | Painel de pendências e prazos, processos, casos e checklists, prorrogações de contratos, afastamentos, rescisões e fluxos de assinatura. |
| Cadastros e qualidade de dados | Pessoas, vínculos e empresas, documentos de cadastro, revisão de conflitos e indicadores de qualidade. Inclui importação com prévia e comandos para ler exportações da V2. |
| Rotinas de DP | Organização do setor, calendário, planejamento de lotes, calculadoras e consulta e gestão de tabelas tributárias. |
| Comunicação | Chat local de equipe, mensagens rápidas, configuração e teste de e-mail, agendamento de envios e conexão com VoxFy. |
| People Analytics | Indicadores de admissões e desligamentos, análise por empresa, perfil de pessoas ativas, retenção, permanência e tipos de contrato. Inclui exploração de registros e relatórios com filtros. |
| Relatórios | Consulta de tabelas, exportação CSV nas telas compatíveis e geração de relatórios Excel quando o motor de exportação está disponível. |
| Administração e suporte | Contas e permissões, gestão de arquivos, trilha de auditoria, backup e recuperação, manuais e ajuda do Analytics. |
| Integrações | Clicksign para assinaturas, SMTP para e-mail, Z-PRO e VoxFy para comunicação, Selecty e SIB Bridge para consultas e sincronização conforme a configuração de cada serviço. |

No banco novo, telas de acompanhamento começam vazias. Os envios e demais ações que dependem de serviços externos exigem configuração própria. O exportador Excel usa Node.js e `@oai/artifact-tool`, que não acompanha o repositório. Em instalações com acesso ao pacote, configure `V3_ARTIFACT_MODULES` com o caminho de `node_modules` que o contém e, se necessário, `V3_NODE` com o caminho do executável Node.js.

## Requisitos

- Python 3.12+
- Node.js e pnpm para a interface web

## Iniciar (Windows PowerShell)

```powershell
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

Mantenha as integrações externas desativadas durante a demonstração. `.env.example` documenta as opções de ambiente e deixa o token vazio. Para aplicar opções ao backend, defina as variáveis no PowerShell antes de executar o comando. O Git ignora os arquivos `.env` locais.

## Conteúdo público

A pasta `dados/`, bancos, backups, logs, documentos, exports, capturas e histórico Git da pasta original não fazem parte desta cópia. Revise arquivos antes de publicar e nunca inclua dados de RH ou credenciais.
