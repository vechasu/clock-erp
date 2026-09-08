"""Transport and template startup costs for the locally served Orders views."""
import gzip
from flask import request

ORDER_ENDPOINTS = {'orders_page', 'order_page', 'wildberries_order_page', 'orders_list_api'}


def register_orders_response(app):
    # Compile before accepting requests, not on the first user's navigation in
    # each worker. This evaluates no template, SQL, session or integration code.
    for name in ('orders.html', '_orders_list_results.html', '_catalog_combobox.html',
                 '_sidebar.html', '_favicon.html', '_orders_timing.html'):
        app.jinja_env.get_template(name)

    @app.after_request
    def compress_orders(response):
        if (request.endpoint not in ORDER_ENDPOINTS or request.method != 'GET'
                or response.status_code != 200 or response.is_streamed
                or response.direct_passthrough
                or response.mimetype not in {'text/html', 'application/json'}
                or response.headers.get('Content-Encoding')
                or response.headers.get('Content-Range')):
            return response
        response.vary.add('Accept-Encoding')
        if request.accept_encodings['gzip'] <= 0:
            return response
        body = response.get_data()
        if len(body) < 1024:
            return response
        compressed = gzip.compress(body, compresslevel=4)
        if len(compressed) < len(body):
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            response.headers.pop('ETag', None)
        return response
