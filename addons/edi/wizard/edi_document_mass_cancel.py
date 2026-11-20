"""EDI document mass cancel wizard"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EdiDocumentMassCancel(models.TransientModel):
    """Cancel several EDI documents at once"""

    _name = "edi.document.mass.cancel"
    _description = "Mass Cancel EDI Documents"

    line_ids = fields.One2many(
        "edi.document.mass.cancel.line", "wizard_id", string="Documents to Cancel"
    )

    @api.model
    def default_get(self, fields_list):
        """Add the selected documents to the list"""
        res = super().default_get(fields_list)
        context = self.env.context
        if "line_ids" in fields_list and context.get("active_model") == "edi.document":
            res["line_ids"] = [
                (0, 0, {"doc_id": doc_id}) for doc_id in context.get("active_ids", [])
            ]
        return res

    def action_confirm(self):
        """Cancel the documents, unless any of them cannot be cancelled"""
        self.ensure_one()
        problem_lines = self.line_ids.filtered("problem")
        if problem_lines:
            raise UserError(self._get_problems_message(problem_lines))

        docs = self.line_ids.doc_id
        if not docs:
            raise UserError(_("There are no documents to cancel."))

        # Cancelling closes the open issues of each document
        for doc in docs:
            doc.action_cancel()
        return {"type": "ir.actions.act_window_close"}

    def _get_problems_message(self, problem_lines):
        """Return the message listing the documents that cannot be cancelled, by problem"""
        problems = problem_lines._fields["problem"]._description_selection(self.env)
        message = _("These documents cannot be cancelled. Remove them from the list and try again.")
        for problem, label in problems:
            lines = problem_lines.filtered(lambda line: line.problem == problem)
            if lines:
                names = ", ".join(lines.doc_id.mapped("name"))
                message += f"\n\n{label}:\n{names}"
        return message


class EdiDocumentMassCancelLine(models.TransientModel):
    """An EDI document to cancel"""

    _name = "edi.document.mass.cancel.line"
    _description = "Mass Cancel EDI Documents: Document to Cancel"

    wizard_id = fields.Many2one(
        "edi.document.mass.cancel", required=True, ondelete="cascade", index=True
    )
    doc_id = fields.Many2one("edi.document", string="Document", required=True, readonly=True)
    doc_type_id = fields.Many2one(related="doc_id.doc_type_id")
    state = fields.Selection(related="doc_id.state")
    processing_state = fields.Selection(related="doc_id.processing_state")
    issue_count = fields.Integer(related="doc_id.issue_count", string="Issues")
    problem = fields.Selection(
        [
            ("done", "Completed: cannot be cancelled"),
            ("cancel", "Already cancelled"),
        ],
        compute="_compute_problem",
    )

    @api.depends("doc_id.state")
    def _compute_problem(self):
        # Documents being processed can be cancelled, like with the Cancel button, as the
        # processing state stays set when processing is interrupted
        for line in self:
            line.problem = line.state if line.state in ("done", "cancel") else False
