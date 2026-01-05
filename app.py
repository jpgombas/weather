#!/usr/bin/env python3
"""
Minimal Dash app to expose a single-page chat UI for the WeatherAgent.
"""
import threading
import atexit
import uuid
from dash import Dash, html, dcc, Input, Output, State
import dash
from agent_code import WeatherAgent

# Create and start the WeatherAgent's MCP server in a background thread
agent = WeatherAgent()
server_thread = threading.Thread(target=agent.start_mcp_server, daemon=True)
server_thread.start()

# Ensure server is stopped on process exit
def _shutdown():
    try:
        agent.stop_mcp_server()
    except Exception:
        pass

atexit.register(_shutdown)

# Server-side buffer for background responses (thread-safe)
PENDING_RESPONSES = {}
PENDING_LOCK = threading.Lock()

# Minimal Dash app
app = Dash(__name__)
app.title = "Weather Chat"

# Minimal blue-themed styles
CONTAINER_STYLE = {
    'maxWidth': '720px',
    'margin': '40px auto',
    'fontFamily': 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial',
}
HEADER_STYLE = {
    'textAlign': 'center',
    'color': '#0b3d91',
    'marginBottom': '20px',
}
CHAT_WINDOW_STYLE = {
    'height': '60vh',
    'border': '1px solid #cfe3ff',
    'borderRadius': '8px',
    'padding': '12px',
    'overflowY': 'auto',
    'background': 'linear-gradient(180deg,#f7fbff, #eef7ff)',
}
USER_BUBBLE = {
    'marginTop': '8px',
    'marginBottom': '8px',
    'padding': '10px 14px',
    'background': '#2b6cb0',
    'color': 'white',
    'borderRadius': '14px',
    'maxWidth': '80%',
    'alignSelf': 'flex-end',
    # Preserve whitespace and newlines inside messages
    'whiteSpace': 'pre-wrap',
    'wordBreak': 'break-word',
} 
ASSISTANT_BUBBLE = {
    'marginTop': '8px',
    'marginBottom': '8px',
    'padding': '10px 14px',
    'background': '#e6f0ff',
    'color': '#0b3d91',
    'borderRadius': '14px',
    'maxWidth': '80%',
    'alignSelf': 'flex-start',
    # Preserve whitespace and newlines inside messages
    'whiteSpace': 'pre-wrap',
    'wordBreak': 'break-word',
} 
INPUT_ROW_STYLE = {
    'display': 'flex',
    'gap': '8px',
    'marginTop': '12px',
}

app.layout = html.Div([
    html.Div([html.H2("Weather Chat", style=HEADER_STYLE),
              html.Div(id='chat-window', style=CHAT_WINDOW_STYLE)], style=CONTAINER_STYLE),
    dcc.Store(id='conversation-store', data=[]),
    dcc.Store(id='pending-responses', data={}),
    dcc.Interval(id='poll-interval', interval=1000, n_intervals=0),
    html.Div([
        dcc.Input(id='input-text', type='text', placeholder='Ask about the weather...', style={'flex': '1', 'padding': '10px', 'fontSize': '16px'}),
        html.Button('Send', id='send-button', n_clicks=0, style={'padding': '10px 16px', 'background': '#0b3d91', 'color': 'white', 'border': 'none', 'borderRadius': '6px'})
    ], style={**CONTAINER_STYLE, **INPUT_ROW_STYLE})
], style={'background': '#f4f8ff', 'height': '100vh', 'paddingTop': '20px'})


@app.callback(
    Output('conversation-store', 'data'),
    Output('input-text', 'value'),
    Input('send-button', 'n_clicks'),
    Input('input-text', 'n_submit'),
    Input('pending-responses', 'data'),
    State('input-text', 'value'),
    State('conversation-store', 'data'),
    prevent_initial_call=True,
)
def handle_send_or_pending(n_clicks, n_submit, pending, value, conversation):
    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]

    conversation = conversation or []

    # Handle send-triggered events
    if triggered_id in ('send-button', 'input-text'):
        # Only proceed if there's text to send
        if not value or not value.strip():
            raise dash.exceptions.PreventUpdate

        # Add user message
        conversation.append({'role': 'user', 'content': value.strip()})

        # Add assistant placeholder message immediately and start background thread to fetch actual response
        placeholder_id = str(uuid.uuid4())
        conversation.append({'role': 'assistant', 'content': 'Thinking...', 'id': placeholder_id, 'status': 'thinking'})

        def _background_chat(user_text, pid):
            try:
                resp = agent.chat(user_text)
            except Exception as e:
                resp = f"Error: {e}"
            with PENDING_LOCK:
                PENDING_RESPONSES[pid] = resp

        threading.Thread(target=_background_chat, args=(value.strip(), placeholder_id), daemon=True).start()

        # Return updated conversation and clear input
        return conversation, ''

    # Handle pending response application
    if triggered_id == 'pending-responses':
        if not pending:
            raise dash.exceptions.PreventUpdate
        for pid, resp in pending.items():
            found = False
            for entry in conversation:
                if entry.get('role') == 'assistant' and entry.get('id') == pid:
                    entry['content'] = resp
                    entry['status'] = 'done'
                    found = True
                    break
            if not found:
                # If no placeholder found, append as a new assistant message
                conversation.append({'role': 'assistant', 'content': resp, 'status': 'done'})
        return conversation, dash.no_update

    # If none of the above, do nothing
    raise dash.exceptions.PreventUpdate


@app.callback(
    Output('pending-responses', 'data'),
    Input('poll-interval', 'n_intervals'),
)
def poll_pending(n_intervals):
    # Copy and clear server-side pending responses
    with PENDING_LOCK:
        if not PENDING_RESPONSES:
            return {}
        copy = dict(PENDING_RESPONSES)
        PENDING_RESPONSES.clear()
    return copy


@app.callback(
    Output('chat-window', 'children'),
    Input('conversation-store', 'data')
)
def update_chat(conversation):
    if not conversation:
        return html.Div(style={'color': '#0b3d91'}, children=[html.P('Say something to the Weather Agent…', style={'margin': '0'})])

    messages = []
    for entry in conversation:
        role = entry.get('role')
        text = entry.get('content')
        # If the agent stored tool results as lists, render them simply
        if isinstance(text, list):
            text = '\n'.join(str(t) for t in text)

        if role == 'user':
            bubble = html.Div(text, style=USER_BUBBLE)
            wrapper = html.Div(bubble, style={'display': 'flex', 'justifyContent': 'flex-end'})
        else:
            # adjust style if assistant message is still thinking
            style = ASSISTANT_BUBBLE.copy()
            if entry.get('status') == 'thinking':
                style.update({'fontStyle': 'italic', 'opacity': '0.7'})
            bubble = html.Div(text, style=style)
            wrapper = html.Div(bubble, style={'display': 'flex', 'justifyContent': 'flex-start'})

        messages.append(wrapper)

    return html.Div(messages, style={'display': 'flex', 'flexDirection': 'column'})


if __name__ == '__main__':
    app.run(debug=True, port=8050)
