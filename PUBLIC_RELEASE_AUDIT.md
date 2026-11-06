# Auditoria da versão pública

Esta pasta é uma cópia de distribuição independente do ambiente privado/local. Ela contém apenas código e recursos de interface; não leva bancos de dados, backups, logs, exports de RH, contatos reais ou histórico privado do Git.

A demonstração começa com banco vazio. Exemplos de cadastro, remetente, destinatário e documento usam nomes e valores sintéticos claramente identificados. Credenciais SMTP e de integrações estão vazias; conexões externas começam desativadas.

O `.gitignore` exclui arquivos de ambiente, credenciais, bancos, documentos, logs, dependências instaladas e saídas de build. Antes de acrescentar novos arquivos, verifique se não contêm dados de clientes, pessoas, empresas ou credenciais.
