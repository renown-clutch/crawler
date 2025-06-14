import logging
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from clutch.db.models import FriendsCrawlTask, Player

app = FastAPI(title="Renown Social Graph Visualizer", version="1.0.0")

# Mount static files directory (we'll create this)
# app.mount("/static", StaticFiles(directory="clutch/static"), name="static")

logger = logging.getLogger(__name__)


class GraphNode(BaseModel):
    id: str
    name: str
    elo: int
    country: Optional[str]
    avatar_url: Optional[str]
    friend_count: int
    steam_id: int


class GraphEdge(BaseModel):
    source: str
    target: str
    mutual: bool


class GraphData(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    metadata: Dict


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the main visualization page."""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Renown Social Graph</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {
            margin: 0;
            padding: 0;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            overflow: hidden;
        }
        
        #container {
            width: 100vw;
            height: 100vh;
            position: relative;
        }
        
        #graph {
            width: 100%;
            height: 100%;
        }
        
        .node {
            cursor: pointer;
            stroke: #fff;
            stroke-width: 2px;
            transition: all 0.3s ease;
        }
        
        .node:hover {
            stroke: #ffd700;
            stroke-width: 4px;
            filter: brightness(1.2);
        }
        
        .node-with-avatar {
            stroke: #333;
            stroke-width: 2px;
        }
        
        .node-image {
            pointer-events: none;
        }
        
        .node-label {
            font-size: 12px;
            font-weight: bold;
            fill: white;
            text-anchor: middle;
            pointer-events: none;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
        }
        
        .link {
            stroke: rgba(255, 255, 255, 0.6);
            stroke-width: 1px;
        }
        
        .link.mutual {
            stroke: rgba(255, 215, 0, 0.8);
            stroke-width: 2px;
        }
        
        #controls {
            position: absolute;
            top: 20px;
            left: 20px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 15px;
            border-radius: 10px;
            font-size: 14px;
            z-index: 1000;
        }
        
        #stats {
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 15px;
            border-radius: 10px;
            font-size: 14px;
            z-index: 1000;
        }
        
        .control-group {
            margin-bottom: 10px;
        }
        
        label {
            display: block;
            margin-bottom: 5px;
        }
        
        input[type="range"] {
            width: 150px;
        }
        
        button {
            background: #667eea;
            color: white;
            border: none;
            padding: 8px 15px;
            border-radius: 5px;
            cursor: pointer;
            margin: 5px;
        }
        
        button:hover {
            background: #5a6fd8;
        }
        
        #tooltip {
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 10px;
            border-radius: 8px;
            font-size: 12px;
            pointer-events: none;
            z-index: 1001;
            opacity: 0;
            transition: opacity 0.3s;
        }
        
        .loading {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: white;
            font-size: 24px;
            z-index: 1000;
        }
        
        .node-highlighted {
            stroke: #ff6b6b !important;
            stroke-width: 4px !important;
            filter: drop-shadow(0 0 10px #ff6b6b);
        }
        
        .link-highlighted {
            stroke: #ff6b6b !important;
            stroke-width: 3px !important;
            opacity: 1 !important;
        }
        
        .node-dimmed {
            opacity: 0.3;
        }
        
        .link-dimmed {
            opacity: 0.1;
        }
        
        #search-container {
            position: absolute;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 1000;
        }
        
        #search-input {
            padding: 8px 12px;
            border: none;
            border-radius: 20px;
            background: rgba(255, 255, 255, 0.9);
            font-size: 14px;
            width: 250px;
            outline: none;
        }
        
        #search-input:focus {
            background: white;
            box-shadow: 0 0 10px rgba(255, 255, 255, 0.5);
        }
    </style>
</head>
<body>
    <div id="container">
        <div id="loading" class="loading">Loading social graph...</div>
        
        <div id="search-container" style="display: none;">
            <input type="text" id="search-input" placeholder="Search players..." oninput="debouncedSearch(event)">
        </div>
        
        <div id="controls" style="display: none;">
            <div class="control-group">
                <label>Min Friends: <span id="minFriendsValue">0</span></label>
                <input type="range" id="minFriends" min="0" max="100" value="0">
            </div>
            <div class="control-group">
                <label>Min Group Size: <span id="minGroupSizeValue">1</span></label>
                <input type="range" id="minGroupSize" min="1" max="50" value="1">
            </div>
            <div class="control-group">
                <label>Node Size: <span id="nodeSizeValue">1.0</span></label>
                <input type="range" id="nodeSize" min="0.5" max="3.0" step="0.1" value="1.0">
            </div>
            <div class="control-group">
                <button onclick="resetZoom()">Reset View</button>
                <button onclick="centerOnLargestGroup()">Center on Largest Group</button>
                <button onclick="toggleFullscreen()">Fullscreen</button>
                <button onclick="redrawVisualization()">Refresh Layout</button>
            </div>
        </div>
        
        <div id="stats" style="display: none;">
            <div><strong>Social Graph Stats</strong></div>
            <div>Visible Nodes: <span id="nodeCount">0</span></div>
            <div>Total Edges: <span id="edgeCount">0</span></div>
            <div>Mutual Connections: <span id="mutualCount">0</span></div>
            <div>Connected Components: <span id="componentCount">0</span></div>
            <div>Largest Group: <span id="largestGroupSize">0</span> players</div>
            <div>Average Friends: <span id="avgFriends">0</span></div>
            <div>Density: <span id="graphDensity">0%</span></div>
        </div>
        
        <svg id="graph"></svg>
        <div id="tooltip"></div>
    </div>

    <script>
        let graphData = null;
        let svg = null;
        let g = null;
        let links = null;
        let nodes = null;
        let searchTimeout = null;
        let nodeMap = new Map(); // Cache for faster lookups
        let edgeMap = new Map(); // Cache for faster edge lookups

        // Initialize the visualization
        async function init() {
            try {
                console.log("Fetching graph data...");
                const response = await fetch('/api/graph-data');
                graphData = await response.json();
                console.log("Graph data loaded:", graphData.metadata);
                
                document.getElementById('loading').style.display = 'none';
                document.getElementById('controls').style.display = 'block';
                document.getElementById('stats').style.display = 'block';
                document.getElementById('search-container').style.display = 'block';
                
                updateStats();
                createVisualization();
            } catch (error) {
                console.error("Error loading graph data:", error);
                document.getElementById('loading').textContent = 'Error loading data: ' + error.message;
            }
        }

        function updateStats() {
            const visibleNodes = graphData.nodes.filter(n => !n.hidden);
            const visibleEdges = graphData.edges.filter(e => !e.hidden);
            const mutualCount = visibleEdges.filter(e => e.mutual).length;
            
            // Calculate connected components
            const components = findConnectedComponents(visibleNodes, visibleEdges);
            const largestComponent = Math.max(...components.map(c => c.length), 0);
            
            // Calculate average friends
            const totalFriends = visibleNodes.reduce((sum, n) => sum + n.friend_count, 0);
            const avgFriends = visibleNodes.length > 0 ? (totalFriends / visibleNodes.length).toFixed(1) : 0;
            
            // Calculate graph density
            const maxPossibleEdges = visibleNodes.length * (visibleNodes.length - 1) / 2;
            const density = maxPossibleEdges > 0 ? ((visibleEdges.length / maxPossibleEdges) * 100).toFixed(2) : 0;
            
            document.getElementById('nodeCount').textContent = visibleNodes.length;
            document.getElementById('edgeCount').textContent = visibleEdges.length;
            document.getElementById('mutualCount').textContent = mutualCount;
            document.getElementById('componentCount').textContent = components.length;
            document.getElementById('largestGroupSize').textContent = largestComponent;
            document.getElementById('avgFriends').textContent = avgFriends;
            document.getElementById('graphDensity').textContent = density + '%';
        }
        
        function findConnectedComponents(nodes, edges) {
            const visited = new Set();
            const components = [];
            const nodeMap = new Map(nodes.map(n => [n.id, n]));
            
            // Build adjacency list
            const adjacency = new Map();
            nodes.forEach(n => adjacency.set(n.id, []));
            edges.forEach(e => {
                if (adjacency.has(e.source.id || e.source) && adjacency.has(e.target.id || e.target)) {
                    adjacency.get(e.source.id || e.source).push(e.target.id || e.target);
                    adjacency.get(e.target.id || e.target).push(e.source.id || e.source);
                }
            });
            
            function dfs(nodeId, component) {
                if (visited.has(nodeId)) return;
                visited.add(nodeId);
                component.push(nodeMap.get(nodeId));
                
                adjacency.get(nodeId).forEach(neighborId => {
                    if (!visited.has(neighborId)) {
                        dfs(neighborId, component);
                    }
                });
            }
            
            nodes.forEach(node => {
                if (!visited.has(node.id)) {
                    const component = [];
                    dfs(node.id, component);
                    if (component.length > 0) {
                        components.push(component);
                    }
                }
            });
            
            return components;
        }

        function createVisualization() {
            const width = window.innerWidth;
            const height = window.innerHeight;

            // Build lookup caches for performance
            nodeMap.clear();
            edgeMap.clear();
            graphData.nodes.forEach(node => nodeMap.set(node.id, node));
            graphData.edges.forEach(edge => edgeMap.set(`${edge.source}-${edge.target}`, edge));

            // Position nodes in a static circular layout
            positionNodesStatically(width, height);

            svg = d3.select("#graph")
                .attr("width", width)
                .attr("height", height);

            // Add zoom behavior (keep this for user control)
            const zoom = d3.zoom()
                .scaleExtent([0.1, 4])
                .on("zoom", (event) => {
                    g.attr("transform", event.transform);
                });

            svg.call(zoom);

            // Main group for all elements
            g = svg.append("g");

            // Create links (no simulation needed)
            links = g.append("g")
                .attr("class", "links")
                .selectAll("line")
                .data(graphData.edges)
                .enter().append("line")
                .attr("class", d => d.mutual ? "link mutual" : "link")
                .attr("x1", d => d.source.x || 0)
                .attr("y1", d => d.source.y || 0)
                .attr("x2", d => d.target.x || 0)
                .attr("y2", d => d.target.y || 0);

            // Create node groups (no dragging for static version)
            const nodeGroups = g.append("g")
                .attr("class", "nodes")
                .selectAll("g")
                .data(graphData.nodes)
                .enter().append("g")
                .attr("class", "node-group")
                .attr("transform", d => "translate(" + d.x + "," + d.y + ")");

            // Add circles for nodes
            nodes = nodeGroups.append("circle")
                .attr("class", d => d.avatar_url ? "node node-with-avatar" : "node")
                .attr("r", getNodeRadius)
                .attr("fill", d => d.avatar_url ? "url(#pattern-" + d.id + ")" : "#69b3a2");

            // Create patterns for profile pictures
            const defs = svg.append("defs");
            
            // Function to create/update patterns
            window.createPatterns = function() {
                // Remove existing patterns
                defs.selectAll("pattern").remove();
                
                graphData.nodes.forEach(node => {
                    if (node.avatar_url) {
                        const radius = getNodeRadius(node);
                        const size = radius * 2;
                        
                        const pattern = defs.append("pattern")
                            .attr("id", "pattern-" + node.id)
                            .attr("x", 0)
                            .attr("y", 0)
                            .attr("patternUnits", "objectBoundingBox")
                            .attr("width", 1)
                            .attr("height", 1);

                        pattern.append("image")
                            .attr("x", 0)
                            .attr("y", 0)
                            .attr("width", size)
                            .attr("height", size)
                            .attr("href", node.avatar_url)
                            .attr("preserveAspectRatio", "xMidYMid slice");
                    }
                });
            }
            
            // Create initial patterns
            createPatterns();

            // Add labels
            const labels = nodeGroups.append("text")
                .attr("class", "node-label")
                .attr("dy", d => getNodeRadius(d) + 15)
                .attr("text-anchor", "middle")
                .text(d => d.name);

            // Add tooltips
            nodeGroups
                .on("mouseover", showTooltip)
                .on("mousemove", moveTooltip)
                .on("mouseout", hideTooltip);

            // Set up controls
            setupControls();
        }

        function positionNodesStatically(width, height) {
            const centerX = width / 2;
            const centerY = height / 2;
            
            // Group nodes by connected components for better layout
            const components = findConnectedComponents(graphData.nodes, graphData.edges);
            
            // Sort components by size (largest first)
            components.sort((a, b) => b.length - a.length);
            
            // Use a more organic layout approach
            const usedPositions = new Set();
            const minDistance = 60; // Minimum distance between nodes
            
            components.forEach((component, componentIndex) => {
                if (component.length === 1) {
                    // Single nodes: scatter around the edges with some randomness
                    const attempts = 50;
                    let placed = false;
                    
                    for (let attempt = 0; attempt < attempts && !placed; attempt++) {
                        const angle = Math.random() * 2 * Math.PI;
                        const radius = Math.min(width, height) * (0.3 + Math.random() * 0.15);
                        const x = centerX + radius * Math.cos(angle);
                        const y = centerY + radius * Math.sin(angle);
                        
                        if (isPositionValid(x, y, usedPositions, minDistance)) {
                            component[0].x = x;
                            component[0].y = y;
                            usedPositions.add(`${Math.round(x/10)},${Math.round(y/10)}`);
                            placed = true;
                        }
                    }
                    
                    // Fallback if no valid position found
                    if (!placed) {
                        const angle = (componentIndex / components.length) * 2 * Math.PI;
                        const radius = Math.min(width, height) * 0.4;
                        component[0].x = centerX + radius * Math.cos(angle);
                        component[0].y = centerY + radius * Math.sin(angle);
                    }
                } else {
                    // Multi-node components: use organic clustering
                    layoutComponentOrganically(component, componentIndex, centerX, centerY, width, height, usedPositions, minDistance);
                }
            });
            
            // Ensure edge coordinates are updated with node positions
            graphData.edges.forEach(edge => {
                const sourceNode = nodeMap.get(edge.source.id || edge.source);
                const targetNode = nodeMap.get(edge.target.id || edge.target);
                
                if (sourceNode && targetNode) {
                    edge.source = sourceNode;
                    edge.target = targetNode;
                }
            });
        }
        
        function isPositionValid(x, y, usedPositions, minDistance) {
            const gridX = Math.round(x / 10);
            const gridY = Math.round(y / 10);
            const gridRadius = Math.ceil(minDistance / 10);
            
            for (let dx = -gridRadius; dx <= gridRadius; dx++) {
                for (let dy = -gridRadius; dy <= gridRadius; dy++) {
                    if (usedPositions.has(`${gridX + dx},${gridY + dy}`)) {
                        return false;
                    }
                }
            }
            return true;
        }
        
        function layoutComponentOrganically(component, componentIndex, centerX, centerY, width, height, usedPositions, minDistance) {
            // Sort nodes by friend count (most connected first)
            component.sort((a, b) => b.friend_count - a.friend_count);
            
            // Determine component center with some offset for variety
            const offsetAngle = (componentIndex * 2.4) % (2 * Math.PI); // Golden angle-ish
            const offsetRadius = componentIndex * 80 + 100;
            const componentCenterX = centerX + offsetRadius * Math.cos(offsetAngle) * 0.7;
            const componentCenterY = centerY + offsetRadius * Math.sin(offsetAngle) * 0.7;
            
            // Keep component center within bounds
            const boundedCenterX = Math.max(100, Math.min(width - 100, componentCenterX));
            const boundedCenterY = Math.max(100, Math.min(height - 100, componentCenterY));
            
            // Place the most connected node at the component center
            if (component.length > 0) {
                component[0].x = boundedCenterX;
                component[0].y = boundedCenterY;
                usedPositions.add(`${Math.round(boundedCenterX/10)},${Math.round(boundedCenterY/10)}`);
            }
            
            // Place other nodes in organic spiral pattern with randomness
            for (let i = 1; i < component.length; i++) {
                const node = component[i];
                let placed = false;
                const maxAttempts = 100;
                
                for (let attempt = 0; attempt < maxAttempts && !placed; attempt++) {
                    // Spiral outward with randomness
                    const spiralRadius = 30 + (i * 15) + (Math.random() - 0.5) * 20;
                    const spiralAngle = (i * 0.618 * 2 * Math.PI) + (Math.random() - 0.5) * 0.5; // Golden angle with noise
                    
                    const x = boundedCenterX + spiralRadius * Math.cos(spiralAngle);
                    const y = boundedCenterY + spiralRadius * Math.sin(spiralAngle);
                    
                    // Check bounds
                    if (x < 50 || x > width - 50 || y < 50 || y > height - 50) {
                        continue;
                    }
                    
                    if (isPositionValid(x, y, usedPositions, minDistance)) {
                        node.x = x;
                        node.y = y;
                        usedPositions.add(`${Math.round(x/10)},${Math.round(y/10)}`);
                        placed = true;
                    }
                }
                
                // Fallback: place in a simple circle if organic placement fails
                if (!placed) {
                    const fallbackAngle = (i / component.length) * 2 * Math.PI;
                    const fallbackRadius = 50 + i * 10;
                    node.x = boundedCenterX + fallbackRadius * Math.cos(fallbackAngle);
                    node.y = boundedCenterY + fallbackRadius * Math.sin(fallbackAngle);
                }
            }
        }

        function getNodeRadius(d) {
            const baseSize = parseFloat(document.getElementById('nodeSize').value);
            const minRadius = 8 * baseSize;
            const maxRadius = 30 * baseSize;
            const maxFriends = Math.max(...graphData.nodes.map(n => n.friend_count));
            
            if (maxFriends === 0) return minRadius;
            
            const scale = d3.scaleLinear()
                .domain([0, maxFriends])
                .range([minRadius, maxRadius]);
            
            return scale(d.friend_count);
        }

        function setupControls() {
            // Min friends filter
            document.getElementById('minFriends').oninput = function() {
                const minFriends = parseInt(this.value);
                document.getElementById('minFriendsValue').textContent = minFriends;
                applyFilters();
            };

            // Min group size filter
            document.getElementById('minGroupSize').oninput = function() {
                const minGroupSize = parseInt(this.value);
                document.getElementById('minGroupSizeValue').textContent = minGroupSize;
                applyFilters();
            };

            // Node size control
            document.getElementById('nodeSize').oninput = function() {
                const size = parseFloat(this.value);
                document.getElementById('nodeSizeValue').textContent = size.toFixed(1);
                updateNodeSizes();
            };

            // Set max for sliders
            const maxFriends = Math.max(...graphData.nodes.map(n => n.friend_count));
            document.getElementById('minFriends').max = maxFriends;
            
            const components = findConnectedComponents(graphData.nodes, graphData.edges);
            const maxGroupSize = Math.max(...components.map(c => c.length));
            document.getElementById('minGroupSize').max = Math.min(maxGroupSize, 50);
        }

        function applyFilters() {
            const minFriends = parseInt(document.getElementById('minFriends').value);
            const minGroupSize = parseInt(document.getElementById('minGroupSize').value);
            
            // Use Set for O(1) lookups instead of array.some()
            const filteredNodeIds = new Set();
            graphData.nodes.forEach(n => {
                if (n.friend_count >= minFriends) {
                    filteredNodeIds.add(n.id);
                }
            });
            
            // Filter edges more efficiently
            const filteredEdges = graphData.edges.filter(e => {
                const sourceId = e.source.id || e.source;
                const targetId = e.target.id || e.target;
                return filteredNodeIds.has(sourceId) && filteredNodeIds.has(targetId);
            });
            
            let finalNodeIds = filteredNodeIds;
            
            // Group size filtering
            if (minGroupSize > 1) {
                const filteredNodes = Array.from(filteredNodeIds).map(id => nodeMap.get(id));
                const components = findConnectedComponents(filteredNodes, filteredEdges);
                const largeComponents = components.filter(c => c.length >= minGroupSize);
                
                finalNodeIds = new Set();
                largeComponents.forEach(component => {
                    component.forEach(node => finalNodeIds.add(node.id));
                });
            }
            
            // Mark visibility efficiently
            graphData.nodes.forEach(n => {
                n.hidden = !finalNodeIds.has(n.id);
            });
            graphData.edges.forEach(e => {
                const sourceId = e.source.id || e.source;
                const targetId = e.target.id || e.target;
                e.hidden = !finalNodeIds.has(sourceId) || !finalNodeIds.has(targetId);
            });
            
            // Update visual elements efficiently
            nodes.style("display", d => finalNodeIds.has(d.id) ? "block" : "none");
            d3.selectAll(".node-label").style("display", d => finalNodeIds.has(d.id) ? "block" : "none");
            links.style("display", d => {
                const sourceId = d.source.id || d.source;
                const targetId = d.target.id || d.target;
                return finalNodeIds.has(sourceId) && finalNodeIds.has(targetId) ? "block" : "none";
            });

            updateStats();
        }

        function updateNodeSizes() {
            // Update node radii
            nodes.attr("r", getNodeRadius);
            
            // Update label positions
            d3.selectAll(".node-label").attr("dy", d => getNodeRadius(d) + 15);
            
            // Recreate patterns with new sizes for nodes with avatars
            createPatterns();
        }

        function showTooltip(event, d) {
            const tooltip = document.getElementById('tooltip');
            tooltip.innerHTML = `
                <strong>${d.name}</strong><br>
                Steam ID: ${d.steam_id}<br>
                ELO: ${d.elo}<br>
                Country: ${d.country || 'Unknown'}<br>
                Friends: ${d.friend_count}
            `;
            tooltip.style.opacity = 1;
        }

        function moveTooltip(event) {
            const tooltip = document.getElementById('tooltip');
            tooltip.style.left = (event.pageX + 10) + 'px';
            tooltip.style.top = (event.pageY + 10) + 'px';
        }

        function hideTooltip() {
            document.getElementById('tooltip').style.opacity = 0;
        }

        function resetZoom() {
            svg.transition().duration(750).call(
                d3.zoom().transform,
                d3.zoomIdentity
            );
        }

        function redrawVisualization() {
            // Recreate static layout
            const width = window.innerWidth;
            const height = window.innerHeight;
            
            svg.attr("width", width).attr("height", height);
            positionNodesStatically(width, height);
            
            // Update node positions
            d3.selectAll(".node-group")
                .attr("transform", d => "translate(" + d.x + "," + d.y + ")");
            
            // Update link positions
            links
                .attr("x1", d => d.source.x || 0)
                .attr("y1", d => d.source.y || 0)
                .attr("x2", d => d.target.x || 0)
                .attr("y2", d => d.target.y || 0);
        }
        
        function centerOnLargestGroup() {
            const visibleNodes = graphData.nodes.filter(n => !n.hidden);
            const visibleEdges = graphData.edges.filter(e => !e.hidden);
            const components = findConnectedComponents(visibleNodes, visibleEdges);
            
            if (components.length === 0) return;
            
            const largestComponent = components.reduce((max, current) => 
                current.length > max.length ? current : max
            );
            
            // Calculate center of largest component
            const centerX = largestComponent.reduce((sum, n) => sum + n.x, 0) / largestComponent.length;
            const centerY = largestComponent.reduce((sum, n) => sum + n.y, 0) / largestComponent.length;
            
            // Transform to center the largest group
            const width = window.innerWidth;
            const height = window.innerHeight;
            const transform = d3.zoomIdentity
                .translate(width / 2 - centerX, height / 2 - centerY)
                .scale(1.5);
            
            svg.transition().duration(1000).call(
                d3.zoom().transform,
                transform
            );
        }
        
        function debouncedSearch(event) {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => searchPlayers(event), 300);
        }
        
        function searchPlayers(event) {
            const query = event.target.value.toLowerCase().trim();
            
            if (query === '') {
                resetHighlighting();
                return;
            }
            
            // Pre-filter visible nodes for better performance
            const visibleNodes = graphData.nodes.filter(n => !n.hidden);
            const matchingNodes = visibleNodes.filter(n => 
                n.name.toLowerCase().includes(query)
            );
            
            if (matchingNodes.length === 0) {
                resetHighlighting();
                return;
            }
            
            const matchingNodeIds = new Set(matchingNodes.map(n => n.id));
            const connectedNodeIds = new Set(matchingNodeIds);
            const highlightedEdges = new Set();
            
            // Find connected nodes and edges more efficiently
            graphData.edges.forEach(e => {
                if (e.hidden) return; // Skip hidden edges
                
                const sourceId = e.source.id || e.source;
                const targetId = e.target.id || e.target;
                
                if (matchingNodeIds.has(sourceId) || matchingNodeIds.has(targetId)) {
                    connectedNodeIds.add(sourceId);
                    connectedNodeIds.add(targetId);
                    highlightedEdges.add(e);
                }
            });
            
            // Apply highlighting efficiently
            nodes.classed("node-highlighted", d => matchingNodeIds.has(d.id))
                 .classed("node-dimmed", d => !connectedNodeIds.has(d.id) && !d.hidden);
            
            links.classed("link-highlighted", d => highlightedEdges.has(d))
                 .classed("link-dimmed", d => !highlightedEdges.has(d) && !d.hidden);
            
            d3.selectAll(".node-label")
                .classed("node-dimmed", d => !connectedNodeIds.has(d.id) && !d.hidden);
        }
        
        function resetHighlighting() {
            nodes.classed("node-highlighted", false)
                 .classed("node-dimmed", false);
            links.classed("link-highlighted", false)
                 .classed("link-dimmed", false);
            d3.selectAll(".node-label").classed("node-dimmed", false);
        }
        

        
        function toggleFullscreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen();
            } else {
                document.exitFullscreen();
            }
        }

        // Window resize handler
        window.addEventListener('resize', function() {
            redrawVisualization();
        });

        // Start the application
        init();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


@app.get("/api/graph-data", response_model=GraphData)
async def get_graph_data(min_friends: int = 0, limit: Optional[int] = None):
    """Get the social graph data for visualization."""
    try:
        logger.info(
            f"Fetching graph data with min_friends={min_friends}, limit={limit}"
        )

        # Get successful friend crawls with their friend counts
        successful_crawls = FriendsCrawlTask.select().where(
            FriendsCrawlTask.crawl_successful == True
        )

        # Build nodes from players with friend data
        nodes = []
        friend_counts = {}
        all_connections = {}

        for crawl_task in successful_crawls:
            friend_count = len(crawl_task.friends_list)

            # Skip if below minimum friends threshold
            if friend_count < min_friends:
                continue

            friend_counts[crawl_task.steam_id] = friend_count
            all_connections[crawl_task.steam_id] = set(crawl_task.friends_list)

            # Get player info
            try:
                player = Player.get(Player.steam_id == crawl_task.steam_id)

                node = GraphNode(
                    id=str(player.steam_id),
                    name=player.name,
                    elo=player.elo,
                    country=player.country,
                    avatar_url=player.avatar_url,
                    friend_count=friend_count,
                    steam_id=player.steam_id,
                )
                nodes.append(node)

            except Player.DoesNotExist:
                logger.warning(f"Player with steam_id {crawl_task.steam_id} not found")
                continue

        # Apply limit if specified
        if limit:
            # Sort by friend count descending to get most connected players
            nodes.sort(key=lambda x: x.friend_count, reverse=True)
            nodes = nodes[:limit]
            # Update the connections dict to only include limited nodes
            node_ids = {node.steam_id for node in nodes}
            all_connections = {
                k: v for k, v in all_connections.items() if k in node_ids
            }

        # Build edges from connections
        edges = []
        processed_edges = set()

        for player_id, friends in all_connections.items():
            for friend_id in friends:
                if friend_id in all_connections:  # Friend is also in our dataset
                    # Create a unique edge identifier (smaller ID first to avoid duplicates)
                    edge_id = tuple(sorted([player_id, friend_id]))

                    if edge_id not in processed_edges:
                        # Check if it's mutual
                        is_mutual = player_id in all_connections.get(friend_id, set())

                        edge = GraphEdge(
                            source=str(player_id),
                            target=str(friend_id),
                            mutual=is_mutual,
                        )
                        edges.append(edge)
                        processed_edges.add(edge_id)

        metadata = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "mutual_edges": len([e for e in edges if e.mutual]),
            "min_friends_filter": min_friends,
            "limit_applied": limit,
            "generated_at": "2024-01-01T00:00:00Z",  # Placeholder
        }

        logger.info(f"Generated graph with {len(nodes)} nodes and {len(edges)} edges")

        return GraphData(nodes=nodes, edges=edges, metadata=metadata)

    except Exception as e:
        logger.error(f"Error generating graph data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_stats():
    """Get basic statistics about the social graph data."""
    try:
        total_players = Player.select().count()
        crawled_players = FriendsCrawlTask.select().count()
        successful_crawls = (
            FriendsCrawlTask.select()
            .where(FriendsCrawlTask.crawl_successful == True)
            .count()
        )

        # Get friend count distribution
        friend_counts = []
        for task in FriendsCrawlTask.select().where(
            FriendsCrawlTask.crawl_successful == True
        ):
            friend_counts.append(len(task.friends_list))

        stats = {
            "total_players": total_players,
            "crawled_players": crawled_players,
            "successful_crawls": successful_crawls,
            "max_friends": max(friend_counts) if friend_counts else 0,
            "avg_friends": sum(friend_counts) / len(friend_counts)
            if friend_counts
            else 0,
            "coverage_percentage": (crawled_players / total_players * 100)
            if total_players > 0
            else 0,
        }

        return stats

    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
