"""Unit tests for document type inference (Gap 3)."""

import pytest

from app.services.ingest_service import infer_document_type


class TestInferDocumentType:
    """Test infer_document_type() heuristic."""

    # --- Filename-based inference ---

    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("Invoice_March_2024.pdf", "invoice"),
            ("INVOICE-001.xlsx", "invoice"),
            ("receipt_amazon.pdf", "receipt"),
            ("Sales_Receipt.png", "receipt"),
            ("contract_v2_signed.docx", "contract"),
            ("SERVICE_CONTRACT.pdf", "contract"),
            ("Q4_report.pptx", "report"),
            ("annual-report-2024.pdf", "report"),
            ("resume_john_doe.pdf", "resume"),
            ("John_CV.docx", "resume"),
            ("bank_statement_jan.pdf", "statement"),
            ("cover_letter.docx", "letter"),
            ("team_memo.txt", "memo"),
            ("project_proposal.docx", "proposal"),
            ("meeting_agenda.md", "agenda"),
            ("board_minutes.pdf", "minutes"),
        ],
    )
    def test_filename_patterns(self, filename, expected):
        result = infer_document_type(filename, "application/pdf", "")
        assert result == expected

    # --- Content-based inference ---

    def test_content_invoice_keywords(self):
        text = "INVOICE #12345\nBill To: Acme Corp\nTotal Due: $500.00"
        result = infer_document_type("document.pdf", "application/pdf", text)
        assert result == "invoice"

    def test_content_receipt_keywords(self):
        text = "RECEIPT\nPayment Received from John Doe\nTransaction ID: TX-9876"
        result = infer_document_type("scan.pdf", "application/pdf", text)
        assert result == "receipt"

    def test_content_contract_keywords(self):
        text = "This AGREEMENT is entered into by and between the parties who hereby agree to the following terms and conditions."
        result = infer_document_type("doc.pdf", "application/pdf", text)
        assert result == "contract"

    # --- MIME type fallback ---

    def test_spreadsheet_mime(self):
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        result = infer_document_type("data.xlsx", xlsx_mime, "some numbers")
        assert result == "spreadsheet"

    def test_csv_mime(self):
        result = infer_document_type("data.csv", "text/csv", "a,b,c")
        assert result == "spreadsheet"

    def test_presentation_mime(self):
        pptx_mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        result = infer_document_type("slides.pptx", pptx_mime, "slide content")
        assert result == "presentation"

    def test_image_mime(self):
        result = infer_document_type("photo.jpg", "image/jpeg", "")
        assert result == "image"

    # --- Unknown ---

    def test_unknown_returns_none(self):
        result = infer_document_type("random_file.bin", "application/octet-stream", "binary data")
        assert result is None

    def test_empty_inputs(self):
        result = infer_document_type("file.dat", "application/octet-stream", "")
        assert result is None

    # --- Priority: filename > content > MIME ---

    def test_filename_takes_priority_over_content(self):
        # Filename says "report", content says "invoice"
        result = infer_document_type(
            "monthly_report.pdf", "application/pdf", "INVOICE #123 Bill To"
        )
        assert result == "report"
