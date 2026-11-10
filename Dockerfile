FROM unipartdigital/udes-tester:14.0

# psycopg2-binary dependencies
RUN apt update && apt install -y python3-dev libpq-dev

# Install pip packages
USER odoo
RUN pip3 install --user \
    coverage==4.5.1 \
    psycopg2-binary==2.8.5 \
    paramiko==5.0.0 \
    ply==3.11 \
    jsonschema==4.26.0

# Prerequisite module installation (without tests)
USER root
RUN odoo-wrapper --without-demo=all -i project,product,stock,sale,sale_management

# Add EDI modules
ADD addons /opt/odoo-addons

# Module tests
CMD ["--test-enable", "-i", "edi,edi_tutorial,edi_product,edi_product_tutorial,edi_stock,edi_stock_tutorial,edi_sale,edi_sale_json,edi_sale_forward_request"]
