(function() {

var w = 1000,
    strip_h = 20,
    h = 600,
    margin = 15,
    totW = w + 2 * margin,
    lblDx = 100,
    axisHeight = 40;

var tScale = d3.scale.linear()
    .range([0, w])
    .domain([0, trace_data.duration]);

var wScale = d3.scale.linear()
    .range([0, w])
    .domain([0, trace_data.duration]);

var zoom = d3.behavior.zoom()
    .scaleExtent([1,1000])
    .x(tScale)
    .on("zoom", draw);

var vis = d3.select("#vis-panel")
    .append("svg:svg")
    .attr("width", totW)
    .attr("height", h)
    .call(zoom)
    .append("svg:g")
    .attr("transform", "translate("+margin+",0)")
    ;

var tAxis = d3.svg.axis().scale(tScale).orient("top")
    .ticks(10).tickFormat(fmtTime(3)).tickSize(20);

var mg = vis.append("g")
    .attr("class", "g-main")
    .attr("transform", "translate(0,"+axisHeight+")")
    ;

var ag = vis.append("g")
    .attr("class", "g-axis")
    .attr("transform", "translate(0,"+axisHeight+")")
    .call(tAxis)
    ;

function draw(){
    tAxis(ag);
    var n_threads = trace_data.threads.length;
    for (var i=0;i< n_threads;++i){
        draw_strip(i);
    }
}

draw();

function load_strip (strip, strip_i) {
    var sg = mg.append("svg:g")
        .attr("class", "g-strip g-strip-"+strip_i)
        .attr("transform", "translate(0, "+(strip_h*strip_i)+")");

    var g = sg.selectAll("g").data(strip.data)
        .enter().append("svg:g")
        .attr("class", function(d){return "gs "+d.cl;})
        .attr("transform", function(d) {
            var x=tScale(d.t); 
            return "translate("+x+",0)"
        });

    g.append("svg:rect")
        .attr("width", function(d){return tScale(d.dt)})
        .attr("height",function(d) {
            return d.cl == 's' ? strip_h-4 : strip_h-6;
        })
        .attr("transform", function(d){
            var y = d.cl == 's' ? 0 : 1;
            return "translate(0,"+y+")";
        })
        .attr("class", "event"); 

    g.append("svg:text")
        .attr("transform", transform)
        .attr("dy", ".35em")
        .attr("class", "event-name")
        .text(function(d) { return d.name; });

    var fmt = fmtTime(1);
    g.append("title").text(function(d){
        return "t="+d.t+", dt="+fmt(d.dt)+", dbg="+d.dbg;});

    function transform(d) {
        return "translate("+tScale(d.dt)/2+"," + strip_h / 2 + ")";
    }
};

function draw_strip (strip_i) {
    var g = mg.selectAll(".g-strip-"+strip_i+" g.gs")
        .attr("transform", function(d) {
            var x=tScale(d.t); 
            return "translate("+x+",0)"
        });

    g.select("rect") .attr("width", function(d){
        return wScale(d.dt)*zoom.scale();
    }) ;

    g.select("text")
        .attr("transform", transform)
        ;

    function transform(d) {
        return "translate("+tScale(d.dt)/2+"," + strip_h / 2 + ")";
    }
};

trace_data.threads.forEach(function(thread, idx){
    d3.select("#side-panel").append("div").attr("class", "strip-label").text(thread.name);

    d3.json(thread.file, function(strip){
        console.log("Loaded json ", strip);
        load_strip(strip, idx);
    });

});

function fmtTime(p) {
    return function(f) {
    var af = Math.abs(f);
    if ( af >= 1.0 ){
        return f.toFixed(p)+"s";
    } else if ( af >= 1e-3 ) {
        return (f*1e3).toFixed(p)+"ms";
    } else if ( af >= 1e-6 ) {
        return (f*1e6).toFixed(p)+"us";
    } else {
        return (f*1e9).toFixed(0)+"ns";
    }
    };
}

})();

