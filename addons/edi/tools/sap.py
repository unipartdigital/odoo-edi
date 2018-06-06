"""SAP helpers for EDI"""

import base64
from odoo.modules.module import get_resource_path
from . import sapidoc

EDI_DC = base64.b64encode(b'EDI_DC')
"""Magic prefix found at the start of every SAP IDoc (after base64 encoding)"""


class SapIDocHeader(sapidoc.model.IDoc):
    """SAP IDoc header parser"""

    class ControlRecord(sapidoc.model.Record):
        IDOCTYP = sapidoc.model.CharacterField(slice(39, 69))
        MESTYP = sapidoc.model.CharacterField(slice(99, 129))

    DataRecord = None
    DataRecords = None


def sap_idoc_type(attachment):
    """Get SAP IDoc type (if any) of an attachment"""

    # Check for magic "EDI_DC" prefix.  Do this before base64 decoding
    # to avoid wasted effort where possible.
    if not attachment.datas.startswith(EDI_DC):
        return None

    # Attempt to parse IDoc header and extract type
    data = base64.b64decode(attachment.datas)
    idoc = SapIDocHeader(data.splitlines()[0])
    return (idoc.control.IDOCTYP, idoc.control.MESTYP)


def SapIDoc(module, *args):
    """Construct SAP IDoc parser from syntax description file

    Construct a SAP IDoc parser model from a syntax description file
    as generated by SAP transation WE63.
    """
    return sapidoc.builder.Model.parse_file(get_resource_path(module, *args))
