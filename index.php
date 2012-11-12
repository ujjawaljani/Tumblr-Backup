<?php

require('core.php');

$authorized = isset($_SESSION["oauth_key"]) && isset($_SESSION["oauth_secret"]);

?>
<!doctype html>
<html>
    <head>
        <title>Tumblr Backup :: Alpha</title>
        <script src="http://cdn.sockjs.org/sockjs-0.3.min.js"></script>
        <style>
            #header {
                margin: 125px auto 50px auto;
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
                margin: 100px auto;
                text-align: center;
            }
        </style>
    </head>
    <body>
        <div id="header"><h1>Tumblr Backup</h1><h3>Alpha</h3></div>
        <div id="main">
            <span id="message"></span>
            <a id="archive" href="#"><br>Download your backup here!</a>
            <span id="prompt">Enter your blog name below to begin the archive process</span><br>
            <form id="blog-form"><input type="text" id="blog"><br><input type="submit" value="Archive"></form>
        </div>
        <div id="footer">
            <span>As an alpha version, this script only downloads individual blogs in an unstyled format. Future versions will include:</span><br>
            <ul>
                <li>Archiving all blogs attached to an account</li>
                <li>Archiving liked posts</li>
                <li>Archiving queued posts</li>
                <li>Archiving submissions</li>
                <li>Archiving private & draft posts</li>
                <li>Raw JSON backup</li>
                <li>Themed HTML backup</li>
                <li>Archiving photos embedded in text posts</li>
                <li>Archiving audio from audio posts</li>
                <li>Archiving video from video posts</li>
            </ul>
        </div>
        <script>
            document.getElementById("blog-form").onsubmit = function() {
                document.getElementById("prompt").style.display = "none";
                document.getElementById("blog-form").style.display = "none";
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
        </script>
    </body>
</html>