"""EDI documents"""

from base64 import b64decode, b64encode
from collections import namedtuple
import logging
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _
from ..tools import NoRecordValuesError

_logger = logging.getLogger(__name__)

AutodetectDocument = namedtuple("AutodetectDocument", ["type", "inputs"])

PROCESSING_STATES = [
    ("waiting", "Waiting"),
    ("preparing", "Preparing"),
    ("executing", "Executing")
]

class IrModel(models.Model):
    """Extend ``ir.model`` to include EDI information"""

    _inherit = "ir.model"

    is_edi_document = fields.Boolean(
        string="EDI Document Model", default=False, help="This is an EDI document model"
    )

    def _reflect_model_params(self, model):
        vals = super()._reflect_model_params(model)
        vals["is_edi_document"] = model._name != "edi.document.model" and issubclass(
            type(model), self.pool["edi.document.model"]
        )
        return vals


class EdiDocumentType(models.Model):
    """EDI document type

    An EDI document type comprises a set of associated EDI record
    types and a model used for parsing attachments into lists of EDI
    records.

    For example: an EDI Product Master Data document type may comprise
    a single associated EDI Product record type and a model capable of
    parsing a custom CSV formatted attachment into a list of EDI
    Product records.
    """

    _name = "edi.document.type"
    _description = "EDI Document Type"
    _order = "sequence, id"

    def _default_sequence_id(self):
        return self.env.ref("edi.sequence_default")

    def _default_project_id(self):
        return self.env.ref("edi.project_default")

    # Basic fields
    name = fields.Char(string="Name", required=True, index=True)
    model_id = fields.Many2one(
        "ir.model",
        string="Document Model",
        domain=[("is_edi_document", "=", True)],
        required=True,
        index=True,
        ondelete="cascade",
    )
    rec_type_ids = fields.Many2many("edi.record.type", string="Record Types")

    # Autodetection order when detecting a document type based upon
    # the set of input attachments.
    sequence = fields.Integer(string="Sequence", help="Autodetection Order")

    # Sequence for generating document names
    sequence_id = fields.Many2one(
        "ir.sequence", string="Document Name Sequence", required=True, default=_default_sequence_id
    )

    # Issue tracker used for asynchronously reporting errors
    project_id = fields.Many2one(
        "project.project", string="Issue Tracker", required=True, default=_default_project_id
    )

    # Control visibility in the UI.
    active = fields.Boolean(
        default=True, string="Active", help="Display in list views or searches."
    )

    # Control behaviour if an error occurs.
    fail_fast = fields.Boolean(
        string="Fail Fast", help="End document execution immediately on error", default=True
    )

    # Control behaviour of records can be created successfully if at least an error occurs
    fail_end = fields.Boolean(
        string="Fail End",
        help="End document execution at the end on error", default=False
    )

    # Optionally enforce filename globs
    enforce_filename = fields.Boolean(
        string="Enforce Document Filenames",
        help="Enforce document filenames follow glob pattern",
        default=False,
    )

    # Controls stages for automatic processing
    processing_level = fields.Selection(
        [
            ("disabled", "Disabled"),
            ("prepare", "Prepare"),
            ("execute", "Execute")
        ],
        default="execute",
        string="Processing Level",
        help="* Disabled: only receive file, don't try to process automatically."\
        "  Users will manually process it."\
        "* Prepare: only prepare after receiving, users will manually execute it."\
        "  This can be used to check that the file is syntactically correct."\
        "* Execute: automatically process, as if `allow_process` is True on `edi.transfer`.",
        copy=False,
        tracking=True,
    )
    execute_sequentially = fields.Boolean(
        string="Sequential Execution Required", help="Document can only be processed if all "\
        "previous documents of the same type have been completed", default=False
    )

    _sql_constraints = [("model_uniq", "unique (model_id)", "The document model must be unique")]

    @api.model
    def autocreate(self, inputs, allow_process=True):
        """Autocreate documents based on input attachments"""
        Document = self.env["edi.document"]
        inputs.write({"res_model": "edi.document", "res_field": "input_ids"})
        input_ids = inputs.ids
        autodetects = []
        for doc_type in self or self.search([]):
            Model = self.env[doc_type.model_id.model]
            if not hasattr(Model, "autotype"):
                continue
            for consume in Model.autotype(inputs):
                autodetects.append(AutodetectDocument(doc_type, consume))
                inputs -= consume
        if inputs:
            if len(self) == 1:
                doc_type_unknown = self
            else:
                doc_type_unknown = self.env.ref("edi.document_type_unknown")
            autodetects.append(AutodetectDocument(doc_type_unknown, inputs))
        docs = Document.browse()
        by_first_input = lambda x: input_ids.index(min(x.inputs.ids))
        for autodetect in sorted(autodetects, key=by_first_input):
            doc_create_data = {
                "doc_type_id": autodetect.type.id,
            }
            if allow_process and self.processing_level in ("prepare", "execute"):
                doc_create_data.update({
                    "processing_state": "waiting"
                })
            doc = Document.create(doc_create_data)
            autodetect.inputs.sudo().write({"res_id": doc.id})
            docs += doc
        return docs

    def autoemit(self):
        """Create, prepare, and execute documents with no inputs"""
        Document = self.env["edi.document"]
        docs = Document.browse()
        for doc_type in self:
            doc = Document.create({"doc_type_id": doc_type.id})
            doc.action_execute()
            docs += doc
        return docs

    @api.onchange("fail_fast", "fail_end")
    def onchange_fail_configurations(self):
        """
        Making sure that fail configurations cannot be both active at same time
        """
        if self.fail_fast:
            self.fail_end = False
        elif self.fail_end:
            self.fail_fast = False


class EdiDocument(models.Model):
    """EDI document

    An EDI document comprises a set of attachments and the
    corresponding set of EDI records.

    For example: an EDI Product Master Data document may comprise a
    single custom CSV formatted attachment and a set of EDI Product
    records representing the new and changed product definitions
    parsed from the CSV file.
    """

    _name = "edi.document"
    _description = "EDI Document"
    _inherit = ["edi.issues", "mail.thread"]

    # Basic fields
    name = fields.Char(
        string="Name",
        index=True,
        copy=False,
        states={"done": [("readonly", True)], "cancel": [("readonly", True)]},
    )
    state = fields.Selection(
        [("draft", "New"), ("cancel", "Cancelled"), ("prep", "Prepared"), ("done", "Completed")],
        string="Status",
        readonly=True,
        index=True,
        default="draft",
        copy=False,
        tracking=True,
    )
    processing_state = fields.Selection(
        PROCESSING_STATES,
        string="Processing Status",
        readonly=True,
        index=True,
        copy=False,
        tracking=True,
    )
    doc_type_id = fields.Many2one(
        "edi.document.type", string="Document Type", required=True, readonly=True, index=True
    )
    prepare_date = fields.Datetime(string="Prepared on", readonly=True, copy=False)
    execute_date = fields.Datetime(string="Executed on", readonly=True, copy=False)
    processing_time_total = fields.Float(
        string="Total processing time", compute="_compute_processing_time"
    )
    note = fields.Text(string="Notes")

    # Communications
    transfer_id = fields.Many2one(
        "edi.transfer",
        string="Transfer",
        readonly=True,
        copy=False,
        index=True,
        groups="edi.group_edi_gateway_view",
    )
    gateway_id = fields.Many2one(
        "edi.gateway",
        related="transfer_id.gateway_id",
        readonly=True,
        store=True,
        copy=False,
        index=True,
        groups="edi.group_edi_gateway_view",
    )

    # Attachments (e.g. CSV files)
    input_ids = fields.One2many(
        "ir.attachment",
        "res_id",
        domain=[("res_model", "=", "edi.document"), ("res_field", "=", "input_ids")],
        string="Input Attachments",
    )
    output_ids = fields.One2many(
        "ir.attachment",
        "res_id",
        domain=[("res_model", "=", "edi.document"), ("res_field", "=", "output_ids")],
        string="Output Attachments",
    )
    input_count = fields.Integer(string="Input Count", compute="_compute_input_count", store=True)
    output_count = fields.Integer(
        string="Output Count", compute="_compute_output_count", store=True
    )
    progress_log = fields.Text(compute="_compute_progress_log", help="Document processing log messages")
    total_records = fields.Integer(string="Total records", compute="_compute_progress_log")
    total_records_processed = fields.Integer(string="Total records processed", compute="_compute_progress_log")

    # Issues (i.e. asynchronously reported errors)
    project_id = fields.Many2one(related="doc_type_id.project_id", readonly=True)
    issue_ids = fields.One2many(inverse_name="edi_doc_id")

    # Record type names (solely for use by views)
    rec_type_names = fields.Char(string="Record Type Names", compute="_compute_rec_type_names")

    fail_fast = fields.Boolean(related="doc_type_id.fail_fast", readonly=True)
    fail_end = fields.Boolean(related="doc_type_id.fail_end", readonly=True)

    # Processing timing statistics
    doc_stat_ids = fields.One2many(
        "edi.document.stats", "doc_id", string="Document Statistics", readonly=True, index=True
    )


    @api.depends("input_ids", "input_ids.res_id")
    def _compute_input_count(self):
        """Compute number of input attachments (for UI display)"""
        for doc in self:
            doc.input_count = len(doc.input_ids)

    @api.depends("output_ids", "output_ids.res_id")
    def _compute_output_count(self):
        """Compute number of output attachments (for UI display)."""
        for doc in self:
            doc.output_count = len(doc.output_ids)

    def _compute_progress_log(self):
        """Compute the processing log messages (for UI display), total records and total processed
        records. Compute on the fly to avoid DB reference, as it may cause deadlock."""
        EDIDocProgress = self.env["edi.document.progress"]
        for rec in self:
            edi_progress = EDIDocProgress.search([("doc_id", "=", rec.id)])
            rec.progress_log = edi_progress.progress_log
            rec.total_records = edi_progress.total_records
            rec.total_records_processed = edi_progress.total_records_processed

    @api.depends(
        "doc_type_id",
        "doc_type_id.rec_type_ids",
        "doc_type_id.rec_type_ids.model_id",
        "doc_type_id.rec_type_ids.model_id.model",
    )
    def _compute_rec_type_names(self):
        """Compute record type name list

        The record type name list is used by the view definitions to
        determine whether or not to display particular record-specific
        pages within the document form view.

        This avoids the need for each record type to define a custom
        boolean field on ``edi.document.type`` to convey the same
        information.

        Note that this hack would be entirely unnecessary if the Odoo
        domain syntax allowed us to express the concept of "visible if
        ``rec_type_ids`` contains <value>".
        """
        self.mapped("doc_type_id.rec_type_ids.model_id.model")
        for doc in self:
            rec_models = doc.mapped("doc_type_id.rec_type_ids.model_id.model")
            doc.rec_type_names = "/%s/" % "/".join(rec_models)

    def _get_state_name(self):
        """Get name of current state"""
        vals = dict(self.fields_get(allfields=["state"])["state"]["selection"])
        return vals[self.state]

    @api.depends("create_date", "execute_date")
    def _compute_processing_time(self):
        """Compute the total time (in hrs) took to process the document from receiving till completion"""
        for rec in self:
            if rec.execute_date:
                diff = rec.execute_date - rec.create_date 
                rec.processing_time_total = diff.total_seconds() / 3600.0 # in hrs for `float_time`
            else:
                rec.processing_time_total = 0

    @api.model_create_multi
    def create(self, vals_list):
        """Create record (generating name automatically if needed).
        Create associated statistics collection record as well."""
        EdiDocumentStats = self.env["edi.document.stats"]
       
        docs = super().create(vals_list)
        for doc in docs:
            if not doc.name:
                doc.name = doc.doc_type_id.sequence_id.next_by_id()
            # Create associated statistics tracker
            EdiDocumentStats.create(
                {"doc_id": doc.id, "state": "waiting", "time_start": doc.create_date}
            )

        return docs

    def copy(self, default=None):
        """Duplicate record (including input attachments)"""
        self.ensure_one()
        new = super().copy(default)
        for attachment in self.input_ids.sorted("id"):
            attachment.copy({"res_id": new.id, "datas": attachment.datas})
        return new

    def lock_for_action(self):
        """Lock document"""
        for doc in self:
            # Obtain a database row-level exclusive lock by writing the record
            doc.state = doc.state

    def inputs(self):
        """Iterate over decoded input attachments"""
        self.ensure_one()
        if not self.input_ids:
            raise UserError(_("Missing input attachment"))
        return ((x.name, b64decode(x.datas)) for x in self.input_ids.sorted("id"))

    def input(self):
        """Get single decoded input attachment"""
        self.ensure_one()
        if len(self.input_ids) > 1:
            raise UserError(_("More than one input attachment"))
        return next(self.inputs())

    def output(self, name, data):
        """Create output attachment"""
        self.ensure_one()
        Attachment = self.env["ir.attachment"]
        attachment = Attachment.create(
            {
                "name": name,
                "name": name,
                "datas": b64encode(data),
                "res_model": "edi.document",
                "res_field": "output_ids",
                "res_id": self.id,
            }
        )
        return attachment

    def execute_records(self):
        """Execute records"""
        self.ensure_one()
        for rec_type in self.doc_type_id.rec_type_ids:
            RecModel = self.env[rec_type.model_id.model]
            recs = RecModel.search([("doc_id", "=", self.id)])
            with self.statistics() as stats:
                recs.execute()
                self.recompute()
            count = len(recs)
            if count:
                _logger.info(
                    "%s executed %s in %.2fs, %d records, %d queries " "(%d per record)",
                    self.name,
                    RecModel._name,
                    stats.elapsed,
                    count,
                    stats.count,
                    (stats.count / count),
                )

    def commit_processing_state_change(self, processing_state=""):
        """
        By default, clean processing state or set with the value of processing state
        """
        self.ensure_one()
        if self.processing_state != processing_state:
            self.update_processing_stats(processing_state)
            self.processing_state = processing_state
            self.env.cr.commit()

    def update_processing_stats(self, processing_state):
        """Track the start/end/total time for waiting/prepare/execute."""
        EdiDocumentStats = self.env["edi.document.stats"]

        self.ensure_one()
        assert(self.processing_state != processing_state)
        vals_stat = {}
        now = fields.Datetime.now()
        
        # Stop the current state's processing time
        current_state = self.processing_state or "waiting"
        current_stat_id = self.doc_stat_ids.filtered(lambda ds: ds.state == current_state)
        current_stat_id.time_end = now
        # Start new state's processing time. If state is empty, current state was stopped
        if processing_state:
            new_stat_id = self.doc_stat_ids.filtered(lambda ds: ds.state == processing_state)
            if new_stat_id:
                # Reset start/end time of an existing (possibly failed) processing stat
                new_stat_id.write({"time_start": False, "time_end": False})
            else:
                EdiDocumentStats.create(
                    {"doc_id": self.id, "state": processing_state, "time_start": now}
                )


    def create_or_update_edi_progress(self, log_message=None, total_records=None, records_processed=None):
        """EDI records processing is triggered in `prepare()` and `execute()` which creates a cursor
        savepoint to atomically commit/rollback the created/updated records. To report the progress
        of long running executions, it is required to continually update the so-far-processed status
        log, **while** the records are being processed. As the EDI document is locked at row-level
        before starting process (in `lock_for_action`), writing to that record will deadlock.
        Instead, an instance of EdiDocumentProgress will keep the progress count (which is linked to
        an EDIDocument). The progress update logs must be committed to the DB to display the
        progress in UI/view.

        In order to commit change to 'another object' (EdiDocumentProgress in this case), create
        another DB cursor. Transaction is committed on closing the cursor. This leaves the
        encompassing transaction (EDI records processing) undisturbed.

        Also set the total edi records and total processed records, if provided. They are added to
        the document's totals as the calls are from linked (multiple) record type objects.
        """
        self.ensure_one()
        towrite = self.env.all.towrite.copy()
        tocompute = self.env.all.tocompute.copy()
        # TODO: use `with api.Environment.manage():` instead of towrite/tocompute manipulation
        with self.pool.cursor() as temp_cr:
            # NOTE: this method does a commit using another cursor, but it is called from inside a
            # savepoint. Odoo core has a bug that it commits all pending writes without considering
            # the cursor. To work around, clear the pending writes & computes here; write the progress
            # and commit; then restore those after.
            temp_self = self.with_env(self.env(cr=temp_cr))
            temp_self.env.all.towrite.clear()
            temp_self.env.all.tocompute.clear()

            EDIDocProgress = temp_self.env["edi.document.progress"]
            edi_progress = EDIDocProgress.search([("doc_id", "=", temp_self.id)])
            if not edi_progress:
                edi_progress = EDIDocProgress.create({"doc_id": temp_self.id})
            # Update with the message, or reset it
            vals = {}
            # Reset all progress if no parameters are passed
            if not log_message and total_records is None and records_processed is None:
                vals["progress_log"] = ""
            if log_message:
                vals["progress_log"] = "\n".join([edi_progress.progress_log or "", log_message]).lstrip()
            if total_records is not None:
                vals["total_records"] = edi_progress.total_records + total_records
            if records_processed is not None:
                vals["total_records_processed"] = edi_progress.total_records_processed + records_processed

            try:
                edi_progress.write(vals)
            except:
                temp_cr.rollback()

        # Restore the original records to write in the main transcation
        self.env.all.towrite = towrite
        self.env.all.tocompute = tocompute

    def reset_progress(self):
        # Reset the existing log messages. Passing no parameters will reset all progress information
        self.create_or_update_edi_progress()

    def action_prepare(self):
        """Prepare document

        Parse input attachments and create corresponding EDI records.
        """
        self.ensure_one()
        # TODO Consider a better way of getting around the permissions
        self = self.sudo()
        # Lock document
        self.lock_for_action()
        # Check document state
        if self.state != "draft":
            raise UserError(_("Cannot prepare a %s document") % self._get_state_name())
        # Set processing status to preparing and commit changes so would be visible that document
        # is being executing
        self.reset_progress()
        self.commit_processing_state_change("preparing")
        # Close any stale issues
        self.close_issues()
        # Create audit trail
        Audit = self.env["edi.attachment.audit"]
        Audit.audit_attachments(self, self.input_ids, body=_("Input attachments"))
        # Prepare document
        _logger.info("Preparing %s", self.name)
        DocModel = self.env[self.doc_type_id.model_id.model]
        env = self.with_context(tracking_disable=True, recompute=False).env
        try:
            # pylint: disable=broad-except
            with self.statistics() as stats, self.env.cr.savepoint():
                self.prepare_date = fields.Datetime.now()
                DocModel.with_env(env).prepare(self.with_env(env))
                self.recompute()
        except Exception as err:
            self.commit_processing_state_change()
            self.raise_issue(_("Preparation failed: %s"), err)
            return False
        # Mark as prepared
        self.state = "prep"
        self.commit_processing_state_change()
        _logger.info("Prepared %s in %.2fs, %d queries", self.name, stats.elapsed, stats.count)
        return True

    def action_unprepare(self):
        """Return Prepared document to Draft state"""
        self.ensure_one()
        # TODO Consider a better way of getting around the permissions
        self = self.sudo()
        # Lock document
        self.lock_for_action()
        # Check document state
        if self.state != "prep":
            raise UserError(_("Cannot unprepare a %s document") % self._get_state_name())
        # Close any stale issues
        self.close_issues()
        # Delete any records
        _logger.info("Unpreparing %s", self.name)
        for rec_type in self.doc_type_id.rec_type_ids.sorted(reverse=True):
            Model = self.env[rec_type.model_id.model]
            Model.search([("doc_id", "=", self.id)]).unlink()
        # Mark as in draft
        self.prepare_date = None
        self.state = "draft"
        self.reset_progress()
        _logger.info("Unprepared %s", self.name)
        return True

    def action_execute(self):
        """Execute document

        Parse EDI records and update database.
        """
        self.ensure_one()
        if not self.check_sequential_exec_allowed():
            return False

        # TODO Consider a better way of getting around the permissions
        self = self.sudo()
        # Lock document
        self.lock_for_action()

        # Automatically prepare document if needed
        if self.state == "draft":
            prepared = self.action_prepare()
            if not prepared:
                return False
        # Check document state
        if self.state != "prep":
            raise UserError(_("Cannot execute a %s document") % self._get_state_name())
        # Set processing status to executing and commit changes so would be visible that document
        # is being executing
        self.reset_progress()
        self.commit_processing_state_change("executing")
        # Close any stale issues
        self.close_issues()
        # Execute document
        _logger.info("Executing %s", self.name)
        DocModel = self.env[self.doc_type_id.model_id.model]
        env = self.with_context(tracking_disable=True, recompute=False).env
        try:
            # pylint: disable=broad-except
            with self.statistics() as stats, self.env.cr.savepoint():
                DocModel.with_env(env).execute(self.with_env(env))
                self.recompute()
        except Exception as err:
            self.commit_processing_state_change()
            self.raise_issue(_("Execution failed: %s"), err)
            return False
        # Create audit trail
        Audit = self.env["edi.attachment.audit"]
        Audit.audit_attachments(self, self.output_ids, body=_("Output attachments"))
        # Mark as processed
        self.execute_date = fields.Datetime.now()
        self.state = "done"
        # Set processing status to inactive and commit changes so would be visible that document
        # is being executed
        self.commit_processing_state_change()
        _logger.info("Executed %s in %.2fs, %d queries", self.name, stats.elapsed, stats.count)
        return True

    def action_cancel(self):
        """Cancel document"""
        self.ensure_one()
        # TODO Consider a better way of getting around the permissions
        self = self.sudo()
        # Lock document
        self.lock_for_action()
        # Check document state
        if self.state == "done":
            raise UserError(_("Cannot cancel a %s document") % self._get_state_name())
        # Close any stale issues
        self.close_issues()
        # Mark as cancelled
        self.state = "cancel"
        _logger.info("Cancelled %s", self.name)
        return True

    def action_view_inputs(self):
        """View input attachments"""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("edi.document_attachments_action")
        action["name"] = _("Inputs")
        action["domain"] = [
            ("res_model", "=", "edi.document"),
            ("res_field", "=", "input_ids"),
            ("res_id", "=", self.id),
        ]
        action["context"] = {
            "default_res_model": "edi.document",
            "default_res_field": "input_ids",
            "default_res_id": self.id,
        }
        return action

    def action_view_outputs(self):
        """View output attachments"""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("edi.document_attachments_action")
        action["name"] = _("Outputs")
        action["domain"] = [
            ("res_model", "=", "edi.document"),
            ("res_field", "=", "output_ids"),
            ("res_id", "=", self.id),
        ]
        action["context"] = {
            "default_res_model": "edi.document",
            "default_res_field": "output_ids",
            "default_res_id": self.id,
        }
        return action

    def unlink(self):
        """Extend unlink to delete any related edi.document.progress records."""
        super().unlink()

        EDIDocProgress = self.env["edi.document.progress"]
        if self:
            edi_progress = EDIDocProgress.search([("doc_id", "in", self.ids)])
            if edi_progress:
                edi_progress.unlink()

    def check_sequential_exec_allowed(self):
        """If document type has sequential execution set, ensure previous documents of same type are
        already processed (done/cancel)."""
        if self.doc_type_id.execute_sequentially:
            previous_docs = self.search_read(
                [
                    ("doc_type_id", "in", self.doc_type_id.ids), # Same document type
                    ("state", "in", ("draft", "prep")),
                    ("id", "<", self.id), # Previous documents _should_ have earlier ids
                ],
                ["name"],
            )
            if previous_docs:
                self.message_post(
                    body=_(
                        f"Not processing {self.name}, waiting for previous documents: "
                        f"{', '.join([doc['name'] for doc in previous_docs])}"
                    ),
                    content_subtype="plaintext",
                )
                return False
        return True


class EdiDocumentModel(models.AbstractModel):
    """EDI document model

    This is the abstract base class for all EDI document models.
    """

    _name = "edi.document.model"
    _description = "EDI Document Model"

    @api.model
    def record_models(self, doc, supermodel="edi.record"):
        """Get EDI record model classes"""
        doc.ensure_one()
        SuperModel = self.env[supermodel]
        return [
            self.env[x]
            for x in doc.doc_type_id.rec_type_ids.mapped("model_id.model")
            if issubclass(type(self.env[x]), type(SuperModel))
        ]

    @api.model
    def record_model(self, doc, supermodel="edi.record"):
        """Get EDI record model class"""
        Models = self.record_models(doc, supermodel=supermodel)
        if not Models:
            return None
        if len(Models) != 1:
            raise ValueError(
                _("Expected singleton record model: %s") % ",".join(x._name for x in Models)
            )
        return Models[0]

    @api.model
    def no_record_values(self):
        """Indicate that a record value generator is not implemented

        This method should be called by any empty placeholder
        convenience methods for constructing EDI record value
        dictionaries (such as are typically found in the base models
        for an EDI document type).
        """
        raise NoRecordValuesError

    @api.model
    def prepare(self, _doc):
        """Prepare document"""
        pass

    @api.model
    def execute(self, doc):
        """Execute document"""
        doc.execute_records()


class EdiDocumentUnknown(models.AbstractModel):
    """Unknown EDI document model"""

    _name = "edi.document.unknown"
    _inherit = "edi.document.model"
    _description = "Unknown Document"

    @api.model
    def prepare(self, doc):
        """Prepare document"""
        super().prepare(doc)
        raise UserError(_("Unknown document type"))


class EdiDocumentProgress(models.Model):
    """Progress tracking for EDI document processing. A separate model is used to write the progress
    in the middle of EDI records processing. The main EDI document is write locked in that duration."""
    
    _name = "edi.document.progress"
    _description = "Progress of EDI document processing"

    doc_id = fields.Integer(
        string="EDI Document",
        required=True,
        readonly=True,
        index=True,
        help="Foreign key to edi.document. Avoided Many2one on purpose for nested sql commit.",
    )
    progress_log = fields.Text(string="Progress log", help="EDI document processing log messages.")
    total_records = fields.Integer(string="Total records", readonly=True)
    total_records_processed = fields.Integer(string="Total records processed", readonly=True)

class EdiDocumentStats(models.Model):
    """EDI Document processing statistics such as start/end of waiting/prepare/execute, total time
    for each processing stage, etc."""

    _name = "edi.document.stats"
    _description = "EDI Document Processing Statistics"

    doc_id = fields.Many2one(
        "edi.document",
        string="EDI Document",
        required=True,
        readonly=True,
        index=True,
        ondelete="cascade",
        help="EDI Document for which the processing statistics are captured.",
    )
    doc_type_id = fields.Many2one("edi.document.type", related="doc_id.doc_type_id", store=True)

    # Processing statistics
    state = fields.Selection(
        PROCESSING_STATES,
        string="Status",
        readonly=True,
        index=True,
    )
    time_start = fields.Datetime(string="Start", readonly=True, help="Start time of current state")
    time_end   = fields.Datetime(string="End", readonly=True, help="End time of current state")
    time_total = fields.Float(
        string="Total time", compute="_compute_total_time",
        store=True, readonly=True, help="Total processing time in hours"
    )

    _sql_constraints = [
        (
            "doc_state_uniq",
            "unique (doc_id, state)",
            "Only one record for a document in a particular state allowed",
        )
    ]

    @api.depends("time_start", "time_end")
    def _compute_total_time(self):
        """Compute the total time (in hrs) the document was in its current state"""
        for rec in self:
            if rec.time_start:
                diff = (rec.time_end or fields.Datetime.now()) - rec.time_start
                rec.time_total = diff.total_seconds() / 3600.0  # in hrs for `float_time` widget

