"""MES Scheduling MCP Server.

A Model Context Protocol (MCP) server that bridges ERP work orders,
a production scheduling engine, and MES shop-floor execution for the
"precision parts factory" integration scenario.

The package exposes a domain layer (models, store, erp, scheduling, mes)
and an MCP server built on FastMCP.
"""

__version__ = "1.0.0"