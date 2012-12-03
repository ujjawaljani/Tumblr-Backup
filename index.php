<!doctype html>
<html>
    <head>
        <title>Tumblr Backup :: Beta</title>
        <script src="http://cdn.sockjs.org/sockjs-0.3.min.js"></script>
        <style>
            #header {
                margin: 75px auto 50px auto;
                text-align: center;
                font-size: 2em;
            }
            #header h1, #header h3 { margin: 0; }
            #header h3 { font-style: italic; }
            #main {
                width: 400px;
                margin: 0 auto;
                text-align: center;
                background: #CCC;
                padding: 15px;
            }
            #archive { display: none; }
            #footer {
                width: 600px;
                margin: 50px auto;
                text-align: center;
            }
            .strike { text-decoration: line-through; }
        </style>
    </head>
    <body>
        <div id="header"><h1>Tumblr Backup</h1><h3>Beta</h3></div>
        <div id="main">
            <span id="message"></span>
            <a id="archive" href="#"><br>Download your backup here!</a>
            <span id="prompt">Enter your blog name below to begin the archive process</span><br>
            <form id="blog-form"><input type="text" id="blog"><input type="submit" value="Archive"></form>
            <a id="already-done" href="/archives">Or browse the already archived tumblrs!</a>
            <a id="ongoing" href="#"><br>Or view all archives in progress!</a>
        </div>
        <div id="footer">
            <span>
                As an beta version, this script may contain bugs.
                Feel free to <a href="http://fugiman.tumblr.com/ask">send me an ask</a> if you need help or have any ideas (or if you found this useful!).
                If you leave the page, or otherwise disconnect from the archive server, the script will still backup your blog.
                You can <a href="/archives">view archived blogs here</a>, or refresh the page and re-enter your blog name to reconnect to archival server.
                <br><br>
                <b>Current features include:</b>
                <br>
                - Raw JSON archive<br>
                - Individual post archive<br>
                - Archiving photos embedded in text posts<br>
                - Archiving audio from audio posts<br>
                <span class="strike">- Archiving video from video posts (only Tumblr & Youtube videos)</span> Temporarily disabled<br>
                <br>
                <b>Planned features include:</b>
                <br>
                - Better theming of archived posts<br>
                - Index page<br>
                - Archiving video from more sources<br>
                <br><br>
                <a href="https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=63WAHWQYH6LGY&lc=US&item_name=Tumblr%20Backup%20Support&item_number=TUMBLRBACKUP&currency_code=USD&bn=PP%2dDonationsBF%3abtn_donate_LG%2egif%3aNonHosted">
                Enjoy this service? A few dollars goes a long way to alleviating server costs!
                </a>
            </span>
        </div>
        <script>
            document.getElementById("blog-form").onsubmit = function() {
                document.getElementById("prompt").style.display = "none";
                document.getElementById("blog-form").style.display = "none";
                document.getElementById("already-done").style.display = "none";
                document.getElementById("ongoing").style.display = "none";
                var conn = new SockJS("http://tumblr-backup.fugiman.com:8080/");
                var done = false;
                conn.onopen  = function() {
                    document.getElementById("message").innerHTML = "Connected to archival server";
                    document.getElementById("main").style.background = "#BCE8F1";
                    conn.send(JSON.stringify({"blog":document.getElementById("blog").value}));
                }
                conn.onmessage = function(e) { 
                    var data = JSON.parse(e.data);
                    if(data.message) {
                        document.getElementById("message").innerHTML = data.message;
                    }
                    if(data.archive) {
                        done = true;
                        document.getElementById("archive").href = data.archive;
                        document.getElementById("archive").style.display = "inline";
                        document.getElementById("main").style.background = "#D6E9C6";
                    }
                }
                conn.onclose  = function() {
                    if(!done) {
                        document.getElementById("message").innerHTML = "Connection to archival server lost!";
                        document.getElementById("main").style.background = "#EED3D7";
                    }
                }
                return false;
            };
            document.getElementById("ongoing").onclick = function() {
                var main = document.getElementById("main");
                var blogs = {};
                var timer = null;
                main.innerHTML = "";
                var conn = new SockJS("http://tumblr-backup.fugiman.com:8080/");
                conn.onopen  = function() {
                    main.style.background = "#BCE8F1";
                    conn.send(JSON.stringify({"show_all":true}));
                    timer = setInterval(function() { conn.send(JSON.stringify({"show_all":true})); }, 60000);
                }
                conn.onmessage = function(e) { 
                    var data = JSON.parse(e.data);
                    if(!(data.blog in blogs)) {
                        blogs[data.blog] = document.createElement("p");
                        main.appendChild(blogs[data.blog]);
                    }
                    blogs[data.blog].innerHTML = data.blog + ": " + data.message;
                }
                conn.onclose  = function() {
                    main.innerHTML = "Connection to archival server lost!";
                    main.style.background = "#EED3D7";
                    if(timer)
                        clearInterval(timer);
                }
                return false;
            };
        </script>
    </body>
</html>