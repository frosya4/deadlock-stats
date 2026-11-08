from app.server.auth import session_cookie


def login(client, account_id=39734273):
    client.cookies.set('dls_session', session_cookie(account_id, client.app.state.session_secret))
