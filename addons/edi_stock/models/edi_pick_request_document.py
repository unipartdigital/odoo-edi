"""EDI stock transfer request documents"""
import csv

from odoo import api, models, _
from odoo.exceptions import ValidationError


class EdiPickRequestDocument(models.AbstractModel):
    """EDI stock transfer request document

    This is the base model for EDI stock transfer request documents.
    Each row represents two collections of EDI records:

    - a collection of EDI stock transfer request records that, in
      turn, each represent a stock transfer that will be created or
      updated when the document is executed, and

    - a collection of EDI stock move request records that, in turn,
      each represent a line item within one of the above a stock
      transfers

    Derived models should implement either :meth:`~.prepare` or
    :meth:`~.pick_request_record_values`.
    """

    _name = "edi.pick.request.document"
    _inherit = "edi.document.sync"
    _description = "Stock Transfer Requests"

    @api.model
    def pick_request_record_model(self, doc, supermodel="edi.pick.request.record"):
        """Get EDI stock transfer request record model class

        Subclasses should never need to override this method.
        """
        return self.record_model(doc, supermodel=supermodel)

    @api.model
    def move_request_record_model(self, doc, supermodel="edi.move.request.record"):
        """Get EDI stock move request record model class

        Subclasses should never need to override this method.
        """
        return self.record_model(doc, supermodel=supermodel)

    @api.model
    def pick_request_record_values_csv(self, doc, data):
        """Construct EDI pick request record value dictionaries

        Must return an iterable of dictionaries, each of which could
        passed to :meth:`~odoo.models.Model.create` in order to create
        an EDI pick request record.
        """
        reader = csv.DictReader(data.decode("utf-8-sig").splitlines())
        self.check_headings_csv(reader.fieldnames)
        picking_type = self.get_picking_type()
        return (
            self.prepare_order_csv(doc, line, picking_type)
            for line in reader
        )

    @api.model
    def move_request_record_values_csv(self, doc, data):
        """Construct EDI move request record value dictionaries

        Must return an iterable of dictionaries, each of which could
        passed to :meth:`~odoo.models.Model.create` in order to create
        an EDI move request record.
        """
        reader = csv.DictReader(data.decode("utf-8-sig").splitlines())
        self.check_headings_csv(reader.fieldnames)
        return (
            self.prepare_move_csv(doc, line)
            for line in reader
        )

    @api.model
    def postprocess_record_values_csv(self, pick_vlist, move_vlist):
        """
        Postprocess pick and move record values.
        This is a hook designed to be extended, for instance - if move or pick value need to be compared
        and merged in place.
        """
        pass

    @api.model
    def prepare_order_csv(self, doc, row, picking_type):
        """Construct EDI pick request record value dictionaries."""
        return self.no_record_values()

    @api.model
    def prepare_move_csv(self, doc, row):
        """Construct EDI move request record value dictionaries."""
        return self.no_record_values()

    @api.model
    def check_headings_csv(self, fieldnames):
        """Check file header field names against defined headers"""
        pass

    @api.model
    def get_picking_type(self):
        """Hook to determine picking type used in prepare_order_csv. DEBT: Should probably utilize the picking types on the document instead."""
        return self.env["stock.picking.type"].browse()

    @api.model
    def prepare(self, doc):
        """Prepare document"""
        super().prepare(doc)
        for fname, data in doc.inputs():
            *__, file_extension = fname.rpartition(".")
            file_extension = file_extension.lower()
            try:
                pick_adapter_method = getattr(self, f"pick_request_record_values_{file_extension}")
                move_adapter_method = getattr(self, f"move_request_record_values_{file_extension}")
                postprocess_adapter_method = getattr(self, f"postprocess_record_values_{file_extension}")
            except AttributeError:
                raise ValidationError(_("File extension %s is not supported by this EDI document type") %file_extension)
            # list() wrappers prevent generator exhaustion if anything consumes the generators in postprocess_adapter_method
            pick_data = list(pick_adapter_method(doc, data))
            move_data = list(move_adapter_method(doc, data))
            postprocess_adapter_method(pick_data, move_data)
            self.pick_request_record_model(doc).prepare(doc, pick_data)
            self.move_request_record_model(doc).prepare(doc, move_data)
