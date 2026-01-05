#!/usr/bin/env python3
"""
Personal Weather Assistant Agent
An MCP-powered agent that uses the weather server to answer natural language queries.
"""

import anthropic
import json
import sys
import os
import logging
from typing import Any, Dict
from weather import MCPStdIOClient, MCPClientError
from config import SYSTEM_PROMPT, TOOLS

LOG_DIR = os.environ.get("LOG_DIR", "logs")
# Ensure logs directory exists
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    pass

client = anthropic.Anthropic()

# MCP server configuration
MCP_SERVER_COMMAND = [sys.executable, "-m", "weather.server"]

# Agent tool logging configuration
# Ensure the logs directory exists (LOG_DIR is preferred) and attach a single FileHandler
AGENT_LOG_FILE = os.path.join(LOG_DIR, "agent_tools.log")
agent_logger = logging.getLogger("weather_agent")
os.makedirs(LOG_DIR, exist_ok=True)
if AGENT_LOG_FILE:
    normalized = os.path.abspath(AGENT_LOG_FILE)
    if not any(isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == normalized for h in agent_logger.handlers):
        handler = logging.FileHandler(normalized)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        agent_logger.addHandler(handler)
    agent_logger.setLevel(logging.INFO)
    agent_logger.propagate = False
else:
    agent_logger.addHandler(logging.NullHandler())


class WeatherAgent:
    def __init__(self):
        self.conversation_history = []
        self.mcp_client = None
        
    def start_mcp_server(self):
        """Start the MCP weather server via an stdio JSON-RPC client"""
        try:
            self.mcp_client = MCPStdIOClient(MCP_SERVER_COMMAND)
            self.mcp_client.start()
            agent_logger.info("MCP Weather Server started")
        except Exception as e:
            agent_logger.exception("Failed to start MCP server")
            print("✗ Failed to start MCP server (see agent log for details).")
            sys.exit(1)
    
    def stop_mcp_server(self):
        """Stop the MCP server client if running"""
        if self.mcp_client:
            self.mcp_client.stop()
            self.mcp_client = None
            agent_logger.info("MCP Weather Server stopped")
            print("✓ MCP Weather Server stopped")

    def call_mcp_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call an MCP tool via the weather server."""
        if not self.mcp_client:
            return {"error": "MCP server not started"}

        try:
            # Call the MCP tool - all tools now handled by the server
            result = self.mcp_client.call_tool(tool_name, parameters)
            
            # Return as structured data
            return {"result": result}
            
        except MCPClientError as e:
            return {"error": str(e)}
    
    
    def chat(self, user_message: str) -> str:
        """Process a user message and return the agent's response"""
        
        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })
        
        # LLMs available from Anthropic
        # claude-haiku-4-5-20251001
        # claude-sonnet-4-20250514
        
        # Agentic loop - keep calling Claude until no more tool uses
        while True:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.conversation_history
            )

            try:
                resp_str = repr(response)
                ai_path = os.path.join(LOG_DIR, "ai_responses.log")
                with open(ai_path, "a", encoding="utf-8") as f:
                    f.write(resp_str)
                    f.write("\n\n" + ("=" * 80) + "\n\n")
            except Exception as e:
                agent_logger.error(f"Failed writing ai_responses.log: {e}")
                        
            # Check if Claude wants to use tools
            if response.stop_reason == "tool_use":
                # Add Claude's response to history
                self.conversation_history.append({
                    "role": "assistant",
                    "content": response.content
                })
                
                # Process each tool use
                tool_results = []
                for block in response.content:
                    if block.type == "text": 
                        print(block.text)
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        # Log parameters used for this tool call
                        agent_logger.info(f"Agent tool call: {tool_name} - Parameters: {str(tool_input)}")
                        
                        # Execute the tool via MCP (no extra validation in this variant)
                        result = self.call_mcp_tool(tool_name, tool_input)

                        # Log full result of the tool call
                        agent_logger.info(f"Agent tool result: {tool_name} - Result: {str(result)}")
                        
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        })
                
                # Add tool results to history
                self.conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })
                
            elif response.stop_reason == "end_turn":
                # Claude is done, extract the text response
                final_response = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_response += block.text
                
                # Add final response to history
                self.conversation_history.append({
                    "role": "assistant",
                    "content": final_response
                })
                
                return final_response
            else:
                return "I encountered an error processing your request."


def main():
    print("=" * 60)
    print("🌤️  Personal Weather Assistant Agent")
    print("=" * 60)
    print("\nThis agent uses MCP to access weather data.")
    print("Ask me about weather forecasts, alerts, or conditions!\n")
    print("Commands: 'quit' or 'exit' to stop\n")
    
    agent = WeatherAgent()
    agent.start_mcp_server()
    
    try:
        while True:
            user_input = input("\n💬 You: ").strip()
            
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("\n👋 Goodbye!")
                break
            
            if not user_input:
                continue
            
            print("\n🤔 Agent thinking...")
            response = agent.chat(user_input)
            print(f"\n🌤️  Agent: {response}")
            
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        agent.stop_mcp_server()


if __name__ == "__main__":
    main()