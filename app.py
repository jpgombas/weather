#!/usr/bin/env python3
"""
Minimal Dash app to expose a single-page chat UI for the WeatherAgent.
"""
import threading
import atexit
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
    State('input-text', 'value'),
    State('conversation-store', 'data'),
    prevent_initial_call=True,
)
def send_message(n_clicks, n_submit, value, conversation):
    # Determine which input triggered the callback
    ctx = dash.callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]

    # Only proceed if there's text to send
    if not value or not value.strip():
        raise dash.exceptions.PreventUpdate

    conversation = conversation or []
    # Add user message
    conversation.append({'role': 'user', 'content': value.strip()})

    # Call the agent synchronously (may block while LLM responds)
    try:
        response = agent.chat(value.strip())
    except Exception as e:
        response = f"Error: {e}"

    # Add assistant message
    conversation.append({'role': 'assistant', 'content': response})

    # Return updated conversation and clear input
    return conversation, ''


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
            bubble = html.Div(text, style=ASSISTANT_BUBBLE)
            wrapper = html.Div(bubble, style={'display': 'flex', 'justifyContent': 'flex-start'})

        messages.append(wrapper)

    return html.Div(messages, style={'display': 'flex', 'flexDirection': 'column'})


if __name__ == '__main__':
    app.run(debug=True, port=8050)
