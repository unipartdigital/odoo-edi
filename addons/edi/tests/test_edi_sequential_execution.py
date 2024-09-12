from .common import EdiCase


class TestEdiSequentialExecution(EdiCase):
    """EDI sequential exexution tests"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        EdiDocumentType = cls.env["edi.document.type"]
        IrModel = cls.env["ir.model"]

        # Create document type
        cls.doc_type = EdiDocumentType.create(
            {
                "name": "Test EDI document",
                "model_id": IrModel._get_id("edi.document.model"),
                "execute_sequentially": True,
            }
        )

    def test_documents_are_processed_sequentially(self):
        """
        Create two edi documents of same type and check that second document can only be
        processed after the first one.
        """
        EdiDocument = self.env["edi.document"]

        doc1 = EdiDocument.create(
            {
                "name": "ToDo list 1",
                "doc_type_id": self.doc_type.id,
                "state": "draft",
            }
        )
        self.assertEqual(doc1.state, "draft")
        doc2 = EdiDocument.create(
            {
                "name": "ToDo list 2",
                "doc_type_id": self.doc_type.id,
                "state": "draft",
            }
        )
        self.assertEqual(doc2.state, "draft")

        # Try to process second document, which shoud remain in 'draft' state
        doc2.action_execute()
        self.assertEqual(doc2.state, "draft")
        # Process the first document and try second document again
        doc1.action_execute()
        self.assertEqual(doc1.state, "done")
        doc2.action_execute()
        self.assertEqual(doc2.state, "done")
        
