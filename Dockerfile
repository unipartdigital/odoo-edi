FROM unipartdigital/odoo-tester:14.0_20261117

# Packages
RUN dnf install -y python3-paramiko python3-ply && dnf clean all
USER odoo
RUN pip3 install --user jsonschema==3.2.0
USER root

# Prerequisite module installation (without tests)
RUN odoo-wrapper --without-demo=all -i project,product,stock,sale,sale_management

# Add EDI modules
ADD addons /opt/odoo-addons

# Module tests
CMD ["--test-enable", "-i", "edi,edi_tutorial,edi_product,edi_product_tutorial,edi_stock,edi_stock_tutorial,edi_sale,edi_sale_json,edi_sale_forward_request"]
