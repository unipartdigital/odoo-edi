FROM unipartdigital/odoo-tester:11.0_20201223

# Packages
RUN dnf install -y python3-paramiko python3-ply && dnf clean all
USER odoo
RUN pip3 install --user jsonschema==3.2.0
USER root

# Prerequisite module installation (without tests)
RUN odoo-wrapper --without-demo=all -i project,document,product,stock,sale,sale_management,sale_stock

# Add EDI modules
ADD addons /opt/odoo-addons

# Module tests
CMD ["--test-enable", "-i", "edi,edi_product,edi_stock,edi_sale,edi_sale_stock,edi_sale_forward_request"]
