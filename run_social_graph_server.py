#!/usr/bin/env python3

import logging

import uvicorn

# Set up logging
logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    print("🌐 Starting Renown Social Graph Visualization Server...")
    print("📊 Server will be available at: http://localhost:8000")
    print("🔗 API endpoints:")
    print("   - Main visualization: http://localhost:8000")
    print("   - Graph data API: http://localhost:8000/api/graph-data")
    print("   - Statistics API: http://localhost:8000/api/stats")
    print("🚀 Starting server...")

    # Import the app after logging setup
    from clutch.social_graph_server import app

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
