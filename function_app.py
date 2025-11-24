import azure.functions as func
from calendars.leadconnector.leadconnector import handle_leadconnector_request
from calendars.leadconnector.book import book_leadconnector_appointment

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ------------------------
# CALENDAR PROVIDER ROUTER
# ------------------------
@app.route(route="calendar/{provider}/get-available-times")
def calendar_router(req: func.HttpRequest) -> func.HttpResponse:
    provider = req.route_params.get("provider")

    if not provider:
        return func.HttpResponse("Missing provider", status_code=400)

    provider = provider.lower()

    if provider == "leadconnector":
        return handle_leadconnector_request(req)

    # more providers coming soon…
    # elif provider == "calendly":
    #     return handle_calendly_request(req)
    # elif provider == "outlook":
    #     return handle_outlook_request(req)

    return func.HttpResponse(
        f"Unknown provider '{provider}'",
        status_code=404
    )


@app.route(route="calendar/leadconnector/book")
def lc_book(req: func.HttpRequest):
    return book_leadconnector_appointment(req)
