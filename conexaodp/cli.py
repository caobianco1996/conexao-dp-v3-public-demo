"""Inicialização e execução direta em Windows ou Linux."""

from __future__ import annotations

import argparse
import hmac
import getpass
import os
import secrets
from pathlib import Path

from .database import connect, initialize
from .security import password_hash


def main() -> None:
    parser = argparse.ArgumentParser(description="Conexão DP V3 Python/SQLite")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("CONEXAO_DP_DB", "dados/conexao_dp_v3.sqlite3")))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("admin")
    commands.add_parser("reset-password", help="define uma nova senha para uma conta existente")
    imported = commands.add_parser("import-v2-csv", help="importa exportações da V2 preservando o SQLite")
    imported.add_argument("--directory", type=Path, default=Path("dados/importacoes_v2"))
    mirror = commands.add_parser("import-v2-mirror", help="importa snapshots operacionais da V2 para SQLite")
    mirror.add_argument("--directory", type=Path, default=Path("dados/v2_mirror"))
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.db)
        print(f"Banco V3 preparado em {args.db.resolve()}")
    elif args.command == "admin":
        initialize(args.db)
        name = input("Nome do ADMIN: ").strip()
        email = input("E-mail do ADMIN: ").strip().lower()
        password = getpass.getpass("Senha (12 caracteres ou mais): ")
        if not name or "@" not in email or len(password) < 12:
            raise SystemExit("Informe nome, e-mail válido e senha com 12 caracteres ou mais.")
        salt = secrets.token_bytes(32)
        with connect(args.db) as db:
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise SystemExit("Conta já existente; nenhuma senha foi alterada.")
            db.execute("INSERT INTO users(name,email,password_salt,password_hash,role) VALUES(?,?,?,?,?)",
                       (name, email, salt, password_hash(password, salt), "ADMIN"))
        print("ADMIN criado.")
    elif args.command == "reset-password":
        initialize(args.db)
        email = input("E-mail da conta: ").strip().lower()
        password = getpass.getpass("Nova senha (12 caracteres ou mais): ")
        confirmation = getpass.getpass("Confirme a nova senha: ")
        if "@" not in email:
            raise SystemExit("Informe um e-mail válido.")
        if len(password) < 12:
            raise SystemExit("A senha deve ter 12 caracteres ou mais.")
        if not hmac.compare_digest(password, confirmation):
            raise SystemExit("As senhas não correspondem; nenhuma alteração foi feita.")

        with connect(args.db) as db:
            user = db.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
            if user is None:
                raise SystemExit("Conta não encontrada; nenhuma alteração foi feita.")
            salt = secrets.token_bytes(32)
            db.execute(
                "UPDATE users SET password_salt=?, password_hash=? WHERE id=?",
                (salt, password_hash(password, salt), user["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        print("Senha alterada. Entre novamente com o e-mail e a nova senha.")
    elif args.command == "import-v2-csv":
        from .import_v2 import import_exports
        counts = import_exports(args.db, args.directory)
        for name, count in counts.items():
            print(f"{name}: {count} linha(s) lidas")
        print("Importação aditiva concluída; cópia de segurança criada ao lado do SQLite.")
    elif args.command == "import-v2-mirror":
        from .import_v2 import import_mirror
        for name, count in import_mirror(args.db, args.directory).items():
            print(f"{name}: {count} registro(s)")
    else:
        from .server import serve as run_server
        initialize(args.db)
        run_server(args.db, args.host, args.port)


if __name__ == "__main__":
    main()
