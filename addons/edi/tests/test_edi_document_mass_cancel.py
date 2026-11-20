"""EDI document mass cancel wizard tests"""

from odoo.exceptions import AccessError, UserError
from .common import EdiCase


class TestEdiDocumentMassCancel(EdiCase):
    """EDI document mass cancel wizard tests"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        IrModel = cls.env["ir.model"]
        DocumentType = cls.env["edi.document.type"]
        cls.Document = cls.env["edi.document"]
        cls.Wizard = cls.env["edi.document.mass.cancel"]
        cls.doc_type = DocumentType.create(
            {
                "name": "Test EDI document",
                "model_id": IrModel._get_id("edi.document.model"),
            }
        )
        cls.new_doc = cls.create_doc("New")
        cls.prepared_doc = cls.create_doc("Prepared")
        cls.prepared_doc.action_prepare()

    @classmethod
    def create_doc(cls, name):
        """Create a document"""
        return cls.Document.create({"name": name, "doc_type_id": cls.doc_type.id})

    def create_wizard(self, docs, user=None):
        """Create the wizard for the documents, as the given user if any"""
        Wizard = self.Wizard.with_user(user) if user else self.Wizard
        return Wizard.with_context(active_model="edi.document", active_ids=docs.ids).create({})

    def test_cancels_new_and_prepared_documents(self):
        """New and prepared documents are cancelled"""
        docs = self.new_doc | self.prepared_doc
        wizard = self.create_wizard(docs)

        wizard.action_confirm()

        self.assertEqual(docs.mapped("state"), ["cancel", "cancel"])

    def test_closes_open_issues(self):
        """Cancelling a document closes its open issues, which the wizard shows"""
        self.new_doc.raise_issue("Test issue for %s", UserError("Test issue"))
        self.assertEqual(self.new_doc.issue_count, 1)
        wizard = self.create_wizard(self.new_doc)
        self.assertEqual(wizard.line_ids.issue_count, 1)
        self.assertFalse(wizard.line_ids.problem)

        wizard.action_confirm()

        self.assertEqual(self.new_doc.state, "cancel")
        self.assertEqual(self.new_doc.issue_count, 0)

    def test_cancels_documents_being_processed(self):
        """
        Documents with a processing state are cancelled, as it stays set when processing is
        interrupted
        """
        docs = self.create_doc("Waiting") | self.create_doc("Executing")
        docs[0].processing_state = "waiting"
        docs[1].processing_state = "executing"
        wizard = self.create_wizard(docs)
        self.assertEqual(wizard.line_ids.mapped("problem"), [False, False])

        wizard.action_confirm()

        self.assertEqual(docs.mapped("state"), ["cancel", "cancel"])

    def test_raises_listing_every_document_that_cannot_be_cancelled(self):
        """
        Completed and cancelled documents are all listed in the error, and no document is
        cancelled
        """
        done_doc = self.create_doc("Completed")
        done_doc.action_execute()
        cancelled_doc = self.create_doc("Cancelled")
        cancelled_doc.action_cancel()
        wizard = self.create_wizard(self.new_doc | done_doc | cancelled_doc)
        self.assertEqual(wizard.line_ids.mapped("problem"), [False, "done", "cancel"])

        with self.assertRaises(UserError) as error:
            wizard.action_confirm()

        message = error.exception.args[0]
        for text in (
            "Completed: cannot be cancelled",
            done_doc.name,
            "Already cancelled",
            cancelled_doc.name,
        ):
            with self.subTest(text=text):
                self.assertIn(text, message)
        self.assertNotIn(self.new_doc.name, message)
        self.assertEqual(self.new_doc.state, "draft")

    def test_only_settings_users_can_mass_cancel(self):
        """Users without Settings access cannot use the wizard"""
        User = self.env["res.users"]
        user = User.create(
            {
                "login": "mass cancel user",
                "name": "Mass Cancel User",
                "groups_id": [(6, 0, self.env.ref("base.group_user").ids)],
            }
        )
        self.assertFalse(user.has_group("base.group_system"))
        with self.assertRaises(AccessError):
            self.create_wizard(self.new_doc, user=user)
