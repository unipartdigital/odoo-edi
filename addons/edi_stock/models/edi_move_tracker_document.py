"""EDI stock tracker documents"""

from odoo import api, models, _
from odoo.exceptions import ValidationError


class EdiMoveTrackerDocument(models.AbstractModel):
    """EDI stock move tracker document

    This is the base model for EDI stock move tracker documents.  Each
    row represents a collection of EDI stock move tracker records
    that, in turn, each represent an EDI stock move tracker that will
    be created or updated when the document is executed.

    All input attachments are parsed to generate a list of potential
    EDI stock move tracker records, represented in the form of a
    values dictionary that could be used to create the EDI stock move
    tracker record.

    Derived models should implement either :meth:`~.prepare` or
    :meth:`~.move_tracker_record_values`.
    """

    _name = "edi.move.tracker.document"
    _inherit = "edi.document.sync"
    _description = "Stock Move Trackers"

    @api.model
    def move_tracker_record_model(self, doc, supermodel="edi.move.tracker.record"):
        """Get EDI stock move tracker record model class

        Subclasses should never need to override this method.
        """
        return self.record_model(doc, supermodel=supermodel)

    @api.model
    def move_tracker_record_values_csv(self, _data):
        """Construct EDI stock move tracker record value dictionaries

        Must return an iterable of dictionaries, each of which could
        passed to :meth:`~odoo.models.Model.create` in order to create
        an EDI stock move tracker record.
        """
        return self.no_record_values()

    @api.model
    def prepare(self, doc):
        """Prepare document"""
        super().prepare(doc)
        for fname, data in doc.inputs():
            *__, file_extension = fname.rpartition(".")
            file_extension = file_extension.lower()
            try:
                adapter_method = getattr(self, f"move_tracker_record_values_{file_extension}")
            except AttributeError:
                raise ValidationError(_("File extension %s is not supported by this EDI document type") %file_extension)
            self.move_tracker_record_model(doc).prepare(doc, adapter_method(data))
