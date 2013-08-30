

var w = 1000,
    h = 600,
    sh = 40,
    timeWindow = 1000,
    tScale = d3.scale.linear().range([0, w]).domain([0, timeWindow]);

var vis = d3.select("#body").append("div")
.attr("class", "chart")
.style("width", (w+200) + "px")
.style("height", h + "px")
.append("svg:svg")
.attr("width", w+200)
.attr("height", h);

var tAxis = d3.svg.axis().scale(tScale).orient("top").ticks(10).tickSize(20);

function load_data (root) {
    var sg = vis.append("svg:g").attr("class", "sched-strip")
        .attr("transform", "translate(100, 40)");

    var g = sg.selectAll("g").data(root)
        .enter().append("svg:g")
        .attr("transform", function(d) { return "translate("+tScale(d.t)+",0)"});

    g.append("svg:rect")
        .attr("width", function(d){return tScale(d.dt)})
        .attr("height", sh)
        .attr("class", "event"); 

    g.append("svg:text")
        .attr("transform", transform)
        .attr("dy", ".35em")
        .attr("class", "event-name")
        .text(function(d) { return d.name; });

    vis.append("g").attr("class", "axis").attr("transform", "translate(100,40)").call(tAxis);
    function transform(d) {
        return "translate("+tScale(d.dt)/2+"," + sh / 2 + ")";
    }
};

var data = [
{"name": "proc1", "t":0, "dt":300},
{"name": "proc2", "t": 300, "dt":400},
    ]; 

load_data(data)

