"""Application-level orchestration for sales reports."""


def build_report_context(
    *,
    all_sales,
    filters,
    filter_records,
    filter_by_source,
    get_columns,
    format_stock_number,
    format_money,
    source_labels,
    status_labels,
    generated_at,
):
    sales = filter_records(all_sales, filters)
    active_sales = [
        sale for sale in sales if not sale.get("is_cancelled")
    ]
    unique_orders = {
        str(sale.get("order_number") or "").strip()
        for sale in active_sales
        if str(sale.get("order_number") or "").strip()
    }
    total_quantity = sum(
        float(
            sale.get(
                "net_quantity_value",
                sale.get("quantity_value") or 0,
            )
        )
        for sale in active_sales
    )

    gross_values = [sale.get("gross_total_amount") for sale in active_sales]
    return_values = [sale.get("returned_amount") for sale in active_sales]
    total_values = [sale.get("total_amount") for sale in active_sales]
    gross_revenue = (
        sum(float(value) for value in gross_values)
        if all(value is not None for value in gross_values)
        else None
    )
    returns_amount = (
        sum(float(value) for value in return_values)
        if all(value is not None for value in return_values)
        else None
    )
    total_revenue = (
        sum(float(value) for value in total_values)
        if all(value is not None for value in total_values)
        else None
    )
    returned_sales = [
        sale
        for sale in active_sales
        if float(sale.get("returned_quantity") or 0) > 0
    ]
    source_sales = filter_by_source(all_sales, filters["source"])

    def unique_values(field):
        return sorted(
            {
                str(sale.get(field) or "").strip()
                for sale in source_sales
                if str(sale.get(field) or "").strip()
            },
            key=str.casefold,
        )

    return {
        "sales": sales,
        "filters": filters,
        "active_source": filters["source"],
        "active_source_label": (
            "Все продажи"
            if filters["source"] == "all"
            else source_labels[filters["source"]]
        ),
        "report_columns": get_columns(filters["source"]) + [
            {
                "key": "returned_quantity_display",
                "label": "Возвращено",
            },
            {
                "key": "returned_at",
                "label": "Дата возврата",
            },
            {
                "key": "return_reason",
                "label": "Причина возврата",
            },
        ],
        "total_sales": len(active_sales),
        "total_records": len(sales),
        "total_cancelled": len(sales) - len(active_sales),
        "total_orders": len(unique_orders),
        "total_quantity": format_stock_number(total_quantity),
        "total_revenue": total_revenue,
        "total_revenue_display": format_money(total_revenue) or "—",
        "gross_revenue": gross_revenue,
        "gross_revenue_display": format_money(gross_revenue) or "—",
        "returns_amount": returns_amount,
        "returns_amount_display": format_money(returns_amount) or "—",
        "total_returned_sales": len(returned_sales),
        "total_returned_quantity": format_stock_number(
            sum(
                float(sale.get("returned_quantity") or 0)
                for sale in returned_sales
            )
        ),
        "sources": unique_values("source"),
        "products": unique_values("product_name"),
        "delivery_methods": unique_values("delivery_method"),
        "regions": unique_values("region"),
        "cities": unique_values("city"),
        "sale_status_labels": status_labels,
        "generated_at": generated_at,
    }
