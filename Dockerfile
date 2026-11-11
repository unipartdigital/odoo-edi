FROM unipartdigital/udes-tester:11.0_99d258617b21c13e69be4cbb091cada5d9e47d55

# Install pip packages (paramiko and ply are already in the image)
USER odoo
RUN pip3 install --user jsonschema==3.2.0

# Prerequisite module installation (without tests)
USER root
RUN odoo-wrapper --without-demo=all -i project,document,product,stock,sale,sale_management,sale_stock

# Add EDI modules
ADD addons /opt/odoo-addons

# Module tests
CMD ["--test-enable", "-i", "edi,edi_product,edi_stock,edi_sale,edi_sale_stock,edi_sale_forward_request"]
