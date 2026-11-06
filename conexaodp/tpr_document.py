"""TPR-39 and administrative evidence, with page breaks and no extra dependency."""
from __future__ import annotations

from datetime import datetime
import textwrap

from .extensions import _pdf_text, _display_date, _format_document


def _pdf(title, sections, paragraphs, footer):
    pages, commands, y = [], [], 750

    def text(x, baseline, value, bold=False, size=9, color="0.08 0.18 0.28"):
        commands.extend(["BT", color + " rg", f"/{'F2' if bold else 'F1'} {size} Tf",
                         f"{x} {baseline} Td", f"({_pdf_text(value)}) Tj", "ET"])

    def new_page():
        nonlocal commands, y
        if commands:
            pages.append(commands)
        commands, y = [], 750
        for offset, line in enumerate(textwrap.wrap(title, width=75)):
            text(48, 805 - offset * 14, line, True, 11)
        text(48, 770, "TPR-39 · Conexão DP", size=8, color="0.35 0.43 0.51")

    new_page()
    for heading, rows in sections:
        if y < 125:
            new_page()
        text(48, y, heading, True, 10)
        y -= 16
        for label, value in rows:
            left = textwrap.wrap(label, width=28) or [""]
            right = textwrap.wrap(str(value or ""), width=60, break_long_words=True) or [""]
            height = max(len(left), len(right)) * 11 + 10
            if y - height < 60:
                new_page()
                text(48, y, heading + " (continuação)", True, 10)
                y -= 16
            commands.extend(["0.80 0.84 0.88 RG", "0.45 w", "0.94 0.96 0.98 rg",
                             f"48 {y-height} 158 {height} re B", "1 1 1 rg",
                             f"206 {y-height} 341 {height} re B"])
            for index, line in enumerate(left):
                text(53, y - 13 - index * 11, line, True, 8.3)
            for index, line in enumerate(right):
                text(212, y - 13 - index * 11, line, size=8.5)
            y -= height
        y -= 18
    for paragraph in paragraphs:
        lines = textwrap.wrap(paragraph, width=102, break_long_words=True) or [""]
        for line in lines:
            if y < 65:
                new_page()
            text(48, y, line, size=8.8)
            y -= 12
        y -= 8
    pages.append(commands)
    page_total = len(pages)
    for index, page in enumerate(pages, 1):
        commands = page
        text(48, 35, footer[:102], size=7, color="0.35 0.43 0.51")
        text(500, 35, f"{index}/{page_total}", size=7)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for page in pages:
        page_id, stream_id = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_id} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {stream_id} 0 R >>".encode("ascii"))
        stream = "\n".join(page).encode("ascii")
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {page_total} >>".encode("ascii")
    out, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(out)


def build_tpr(context):
    contract, representative = context["contract"], context["representative"]
    first, final = _display_date(contract["first_due_date"]), _display_date(context["proposedDueDate"])
    sections = [
        ("1. IDENTIFICAÇÃO DAS PARTES", [
            ("Empregadora", contract["employer_name"]), ("CNPJ", _format_document(contract["employer_cnpj"], "CNPJ")),
            ("Endereço", contract["employer_address"]), ("Trabalhador(a)", contract["person_name"]),
            ("CPF", _format_document(contract["cpf"], "CPF")), ("Função", contract["worker_function"]),
            ("Matrícula / ID Folha", contract["registration"]), ("Admissão", _display_date(contract["admission_date"])),
        ]),
        ("2. CONTRATO E PRESTAÇÃO DE SERVIÇOS", [
            ("Vínculo", contract["contract_type"]), ("1º vencimento", first),
            ("Prazo adicional", "90 (noventa) dias corridos"), ("Nova data final proposta", final),
            ("Tomadora", contract["tomadora_name"]), ("CNPJ da tomadora", _format_document(contract["tomadora_cnpj"], "CNPJ")),
            ("Local da prestação", contract["work_location"]), ("Demanda complementar", contract["complementary_demand"]),
        ]),
        ("3. REPRESENTANTE DA EMPREGADORA", [
            ("Nome", representative["name"]), ("CPF", _format_document(representative["cpf"], "CPF")),
            ("Cargo / função", representative["role"]), ("Canal de assinatura", representative["channel"]),
            ("Ordem", "1 · representante; 2 · trabalhador"),
        ]),
    ]
    paragraphs = [f"As partes ajustam a prorrogação do contrato temporário por 90 (noventa) dias corridos, a contar do primeiro vencimento em {first}, fixando a nova data final em {final}.",
                  "Permanecem inalteradas e ratificadas todas as demais cláusulas e condições do contrato original que não conflitarem com este termo.",
                  "A manifestação de vontade será formalizada por assinatura eletrônica na Clicksign. Data, hora, evidências de autenticação, integridade, signatários e identificação definitiva do envelope constarão da trilha de auditoria emitida pela plataforma.",
                  "EMPREGADORA / REPRESENTANTE: ______________________________________________",
                  "TRABALHADOR(A): __________________________________________________________"]
    return _pdf("TERMO DE PRORROGAÇÃO DE CONTRATO DE TRABALHO TEMPORÁRIO", sections, paragraphs,
                f"Preparado em {context['preparedAt']} · Controle {context['reference']} · Assinaturas na trilha Clicksign")


def build_non_signature(context, envelope_id, reason):
    contract = context["contract"]
    sections = [("CONTROLE DO PACOTE", [("Trabalhador(a)", contract["person_name"]),
                  ("Matrícula", contract["registration"]), ("Admissão", _display_date(contract["admission_date"])),
                  ("1º vencimento", _display_date(contract["first_due_date"])), ("Envelope Clicksign", envelope_id),
                  ("Ocorrência", reason)])]
    return _pdf("REGISTRO ADMINISTRATIVO DE AUSÊNCIA DE ASSINATURA", sections,
                ["O pacote foi consultado e o cancelamento foi confirmado pelo provedor antes deste registro.",
                 "Este documento preserva a ocorrência administrativa. Não substitui um termo assinado, não presume concordância do trabalhador e não altera o vencimento informado pelo SIB."],
                f"Gerado em {datetime.now().astimezone().isoformat(timespec='seconds')} · Controle {context['reference']}")
