"""The three demo agents: orchestrator, search, summarizer.

They cooperate to answer one question — orchestrator plans and delegates over
A2A, search calls a tool over MCP and extracts facts, summarizer writes the final
answer. Each step is wrapped with the Argos SDK so real spans flow through the
pipeline. The agents are NOT the product — they exist only to give Argos
something genuine to trace.
"""
