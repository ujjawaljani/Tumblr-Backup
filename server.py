# -*- coding: utf-8 -*-
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol, Factory
from twisted.web.client import HTTPConnectionPool, Agent, ResponseDone
from twisted.web.http_headers import Headers
import json, time, urllib, urlparse, hashlib, hmac, binascii, random, os, datetime, shutil, zipfile

API_KEY = "RfTll0TPYGVm3kTbauZRH5QVAgBH3UAkQcpPmHDIMaWEa9xtY8"
API_SECRET = "BS3qBplNbJGzXkhNANnyrd9AVAx4aE0oAQ5tAMBHZWGzpAdiHy"
EMPTY_DIR = "/tmp/empty"

if not os.path.exists(EMPTY_DIR):
    os.makedirs(EMPTY_DIR)

#TESTING
API_KEY = "Jg7jMyKwiXTWCCV863WcifugEl4uerTsaSGfK91ft8OkNiARcN"
API_SECRET = "Xz3og4GfV6eHjyIiJLwlbkGY2mB3BQ0FqExHJmRJD8LUFo0cay"
CONSUMER_KEY = "zlt6K3c33CvIMiG8fcQZG6CzqgZajSUp4ZafNcgHDnNSnqWBHG"
CONSUMER_SECRET = "xckN9FYFXX4dExQoOpigDxfpAtkLX1X8qIUxoCmDakmFEU3c03"

def normalize(s, encoding):
    if not isinstance(s, basestring):
        return s
    elif isinstance(s, unicode):
        return s.encode('utf-8', 'ignore')
    else:
        if s.decode('utf-8', 'ignore').encode('utf-8', 'ignore') == s: # Ensure s is a valid UTF-8 string
            return s
        else: # Otherwise assume it is Windows 1252
            return s.decode(encoding, 'ignore').encode('utf-8', 'ignore')

def safe_format(template, *args):
    args = list(args)
    safe_args = []
    for a in args:
        safe_args.append(normalize(a, "cp1252"))
    return template.format(*safe_args)

def generate_page(post, title, body):
    return safe_format("""<!doctype html>
<html>
    <head>
        <title>Tumblr Backup</title>
    </head>
    <body>
        <div>
            <div>{}</div>
            <div>{}</div>
        </div>
    </body>
</html>""", title, body)

class TumblrDeliverer(Protocol):
    def __init__(self, callback, json):
        self.callback = callback
        self.json = json
        self.buf = "";
    
    def dataReceived(self, data):
        self.buf += data
    
    def connectionLost(self, reason):
        reason.trap(ResponseDone)
        data = json.loads(self.buf)["response"] if self.json else self.buf
        self.callback(data)

class TumblrDownloader(object):
    def __init__(self, factory, public, secret, json=False):
        self.factory = factory
        self.public = public
        self.secret = secret
        self.json = json
        self.finished = Deferred()
        self._request("user/info", self.info, True)
        self.factory.broadcast(self.publish, "Fetching User Info...")
    
    def _request(self, suffix, callback):
        url = "http://api.tumblr.com/v2/"+suffix
        headers = None
        
        base_url, chaff, query = url.partition("?")
        params = query if query else {}
        oparams = {
            "oauth_consumer_key": API_KEY,
            "oauth_token": self.public,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": time.time(),
            "oauth_nonce": hex(time.time()),
            "oauth_version": "1.0",
        }
        params.update(oparams)
        params = urllib.urlencode(sorted(params.items()))
        base = "GET&"+urllib.quote(base_url)+"&"+urllib.quote(params)
        oparams["oauth_signature"] = binascii.b2a_base64(hmac.new(API_SECRET+"&"+self.secret, base, hashlib.sha1).digest())[:-1] # Remove \n
        headers = Headers({
            "Authorization": [urllib.urlencode(sorted(oparams.items()))]
        })
        
        d = Agent(reactor, pool=self.factory.pool).request("GET", url, headers, None)
        d.addCallback(self._deliver, callback)
        d.addErrback(self._error)
        return d
    
    def _error(self, failure):
        raise failure
    
    def _deliver(self, response, callback):
        response.deliverBody(TumblrDeliverer(callback, True))
    
    def _genfolder(self):
        folder = safe_format("/tmp/{}{!s}/", self.user["name"], random.randint(100000,999999))
        if os.path.exists(folder):
            return self._genfolder()
        os.makedirs(folder)
        return folder
    
    def messageReceived(self, message):
        blog = self.user["blogs"][self.current_blog]
        self.factory.publish(self.public, safe_format("{} for \"{}\"", message, blog["name"]))
    
    def info(self, user):
        self.user = user["user"]
        for blog in self.user["blogs"]:
            blog["url"] = urlparse.urlparse(blog["url"]).hostname
        self.folder = self._genfolder()
        self.current_blog = 0
        self.download_blog()
    
    def download_blog(self):
        blog = self.user["blogs"][self.current_blog]
        url = blog["url"] if "." in blog["url"] else blog["url"]+".tumblr.com"
        self.factory.subscribe(self, url)
        TumblrBlogDownloader(self.factory, url, self.json).finished.addCallback(self.finished_blog)
    
    def finished_blog(self, folder):
        blog = self.user["blogs"][self.current_blog]
        url = blog["url"] if "." in blog["url"] else blog["url"]+".tumblr.com"
        self.current_blog += 1
        self.parent.factory.unsubscribe(self, url)
        os.renames(folder, safe_format("{}/{}/", self.folder, blog["name"]))
        if self.current_blog >= len(self.user["blogs"]):
            self.done()
        else:
            self.download_blog()
    
    def done(self):
        self.factory.publish(self.public, "Finished")
        self.finished.callback(self.folder)

class TumblrBlogDownloader(object):
    def __init__(self, factory, url, json=False):
        self.factory = factory
        self.url = url
        self.json = json
        self.blog = None
        self.finished = Deferred()
        self.posts = []
        self.images = []
        self.audio = []
        self.video = []
        self.image = 0
        self.folder = self._genfolder()
        self._request("info", self.blog_info)
        self.factory.publish(self.url, "Fetching Blog Info")
    
    def _genfolder(self):
        folder = safe_format("/tmp/{}{!s}", self.url, random.randint(100000,999999))
        if os.path.exists(folder):
            return self._genfolder()
        os.makedirs(folder)
        return folder
    
    def _request(self, suffix, callback, raw=False):
        if raw:
            url = suffix
        else:
            url = safe_format("http://api.tumblr.com/v2/blog/{}/{}", self.url, suffix)
            url += "&api_key="+API_KEY if "?" in url else "?api_key="+API_KEY
        
        d = Agent(reactor, pool=self.factory.pool).request("GET", url, None, None)
        d.addCallback(self._deliver, callback, not raw)
        d.addErrback(self._error)
        return d
    
    def _error(self, failure):
        raise failure
    
    def _deliver(self, response, callback, json):
        response.deliverBody(TumblrDeliverer(callback, json))
    
    def blog_info(self, info):
        self.blog = info["blog"]
        self._request("avatar/512", self.avatar_info)
        self.factory.publish(self.url, "Fetching Avatar URL")
    
    def avatar_info(self, info):
        self.blog["avatar_url"] = info["avatar_url"]
        self._request(self.blog["avatar_url"].encode("UTF-8"), self.avatar, True)
        self.factory.publish(self.url, "Fetching Avatar")
    
    def avatar(self, avatar):
        suffix = self.blog["avatar_url"].split(".")[-1]
        with open(safe_format("{}/avatar.{}", self.folder, suffix), "wb") as f:
            f.write(avatar)
        self.download_posts()
    
    def download_posts(self):
        post = len(self.posts)
        self._request(safe_format("posts?notes_info=true&offset={!s}", post), self.post_data)
        self.factory.publish(self.url, safe_format("Fetching Posts ({!s}/{!s})", post, self.blog["posts"]))
    
    def post_data(self, posts):
        posts = posts["posts"]
        self.posts.extend(posts)
        if not self.json:
            for post in posts:
                timestamp = datetime.datetime.fromtimestamp(post["timestamp"])
                folder = timestamp.strftime("%Y_%m-%B")
                file = timestamp.strftime("%d_%H-%M-%S")+".html"
                data = ""
                
                if post["type"] == "text":
                    title = safe_format("<h1>{}</h1>", post["title"]) if "title" in post and post["title"] else ""
                    data = generate_page(post, title, post["body"])
                elif post["type"] == "quote":
                    data = generate_page(post, "", safe_format("<blockquote><p>{}</p><small>{}</small></blockquote>", post["text"],post["source"]))
                elif post["type"] == "link":
                    data = generate_page(post, safe_format("<a href='{}'><h1>{}</h1></a>", post["url"],post["title"]), post["description"])
                elif post["type"] == "answer":
                    data = generate_page(post, safe_format("<blockquote><p>{}</p><small><a href='{}'>{}</a></small></blockquote>", post["question"],post["asking_url"],post["asking_name"]), post["answer"])
                elif post["type"] == "video":
                    embed = ""
                    width = 0
                    for player in post["player"]:
                        if player["width"] > width:
                            width = player["width"]
                            embed = player["embed_code"]
                    # TODO: Extract video url, add to download queue, and replace embed code with VIDEO tag
                    data = generate_page(post, embed, post["caption"])
                elif post["type"] == "audio":
                    embed = post["player"]
                    # TODO: Extract audio url, add to download queue, and replace embed code with AUDIO tag
                    title = safe_format("<h1>{} by {}</h1>", 
                        post["track_name"] if "track_name" in post else "",
                        post["artist"] if "artist" in post else ""
                    )
                    body = safe_format("<p><img src='{}' /></p><p style='vertical-align: middle'>{} {!s} plays</p>{}",
                        post["album_art"] if "album_art" in post else "",
                        embed,
                        post["plays"],
                        post["caption"] if "caption" in post else ""
                    )
                    data = generate_page(post, title, body)
                elif post["type"] == "photo":
                    embed = ""
                    for photo in post["photos"]:
                        width = 0
                        url = ""
                        for size in photo["alt_sizes"]:
                            if size["width"] > width:
                                width = size["width"]
                                url = size["url"]
                        if url:
                            self.images.append(url)
                            suffix = url.split(".")[-1]
                            if suffix not in ("png","jpg","gif"):
                                suffix = "png"
                            embed += safe_format("<img src='../images/image{:>06d}.{}' alt='{}' />", len(self.images), suffix, photo["caption"])
                    data = generate_page(post, embed, post["caption"])
                elif post["type"] == "chat":
                    text = "<table>"
                    for line in post["dialogue"]:
                        text += safe_format("<tr><td style='font-weight:bold'>{}</td><td>{}</td></tr>", line["name"], line["phrase"])
                    text += "</table>"
                    data = generate_page(post, safe_format("<h1>{}</h1>", post["title"]), text)
                else:
                    raise Exception("Invalid type - "+post["type"])
                
                if not os.path.exists(safe_format("{}/{}/", self.folder, folder)):
                    os.makedirs(safe_format("{}/{}/", self.folder, folder))
                with open(safe_format("{}/{}/{}", self.folder, folder, file), "w") as f:
                    f.write(data)
        if len(self.posts) >= self.blog["posts"]:
            with open(safe_format("{}/posts.json", self.folder), "w") as f:
                f.write(json.dumps(self.posts))
            if self.json:
                self.done()
            else:
                os.makedirs(safe_format("{}/images/", self.folder))
                self.download_images()
        else:
            self.download_posts()
    
    def download_images(self):
        self._request(self.images[self.image].encode("UTF-8"), self.image_data, True)
        self.factory.publish(self.url, safe_format("Fetching Images ({!s}/{!s})", self.image+1, len(self.images)))
    
    def image_data(self, image):
        suffix = self.images[self.image].split(".")[-1]
        if suffix not in ("png","jpg","gif"):
            suffix = "png"
        self.image += 1
        with open(safe_format("{}/images/image{:>06d}.{}", self.folder, self.image, suffix), "wb") as f:
            f.write(image)
        if self.image >= len(self.images):
            self.done()
        else:
            self.download_images()
    
    def done(self):
        self.factory.publish(self.url, "Finished")
        self.finished.callback(self.folder)

class TumblrUser(Protocol):
    def __init__(self):
        self.channel = None
    
    def dataReceived(self, line):
        if self.channel:
            return
        data = json.loads(line.strip())
        data["public"] = CONSUMER_KEY
        data["secret"] = CONSUMER_SECRET
        json_only = "json" in data and data["json"]
        self.channel = None
        if "blog" in data:
            url = data["blog"] if "." in data["blog"] else data["blog"]+".tumblr.com"
            self.channel = url
            TumblrBlogDownloader(self.factory, url, json_only).finished.addCallback(self.done)
        elif "public" in data and "secret" in data:
            self.channel = data["public"]
            TumblrDownloader(self.factory, data["public"], data["secret"], json_only).finished.addCallback(self.done)
        else:
            return self.done(EMPTY_DIR)
        self.factory.subscribe(self, self.channel)
    
    def messageReceived(self, message):
        self.transport.write(json.dumps({"message":message}))
    
    def done(self, folder):
        if self.channel:
            self.factory.unsubscribe(self, self.channel)
            self.channel = None
        head, tail = os.path.split(folder)
        while head and not tail:
            head, tail = os.path.split(head)
        while os.path.exists(safe_format("archives/{}.zip", tail)):
            tail += random.randint(0,9)
        archive = safe_format("archives/{}.zip", tail)
        zf = zipfile.ZipFile(archive, "w")
        for root, dirs, files in os.walk(folder):
            dir = root
            parts = []
            while not os.path.samefile(folder, dir):
                dir, part = os.path.split(dir)
                parts.append(part)
            parts.reverse()
            dir = os.path.join(*parts) if parts else ""
            for file in files:
                zf.write(os.path.join(root, file), os.path.join(dir, file))
        zf.close()
        if folder != EMPTY_DIR:
            shutil.rmtree(folder)
        self.transport.write(json.dumps({"archive":archive}))

class TumblrServer(Factory):
    protocol = TumblrUser
    def __init__(self):
        self.pool = HTTPConnectionPool(reactor)
        self.pool.retryAutomatically = True
        self.pool.maxPersistentPerHost = 25
        self.channels = {}
    
    def subscribe(self, p, channel):
        if channel in self.channels:
            self.channels[channel].append(p)
        else:
            self.channels[channel] = [p]
    
    def unsubscribe(self, p, channel):
        if channel in self.channels:
            self.channels[channel].remove(p)
            if not self.channels[channel]:
                del self.channels[channel]
    
    def publish(self, channel, message):
        if channel in self.channels:
            for p in self.channels[channel]:
                p.messageReceived(message)

reactor.listenTCP(8080, TumblrServer())
reactor.run()