"""Administración por línea de comandos.

  python admin.py empresa "Hotel Gloria" GLO 8
      Crea la empresa, 8 códigos aleatorios (GLO-K7Q2, ...) y el enlace del gerente.
"""
import argparse
import os

import db


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("empresa", help="crear una empresa con sus códigos")
    e.add_argument("nombre")
    e.add_argument("prefijo", help="3 o 4 letras, por ejemplo GLO")
    e.add_argument("cantidad", type=int)
    args = p.parse_args()

    db.iniciar()
    token, codigos = db.crear_empresa(args.nombre, args.prefijo, args.cantidad)
    base = os.environ.get("URL_BASE", "https://TU-DOMINIO")
    print(f"Empresa: {args.nombre}")
    print(f"Enlace del gerente: {base}/gerente?token={token}")
    print("Códigos de alumnos (uno por persona):")
    for c in codigos:
        print("  ", c)


if __name__ == "__main__":
    main()
