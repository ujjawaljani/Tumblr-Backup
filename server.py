# -*- coding: utf-8 -*-
from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks, returnValue
from twisted.internet.protocol import Protocol, ProcessProtocol, Factory
from twisted.internet.error import ProcessTerminated
from twisted.internet.utils import getProcessValue
from twisted.python import log
from twisted.web.client import HTTPConnectionPool, Agent, ResponseDone
from twisted.web.http import PotentialDataLoss
from twisted.web.http_headers import Headers
from twisted.web.static import File
from txsockjs.factory import SockJSResource
from StringIO import StringIO
import json, time, urllib, urlparse, hashlib, hmac, binascii, random, os, datetime, shutil, zipfile, re, locale, base64, stat

API_KEY = "RfTll0TPYGVm3kTbauZRH5QVAgBH3UAkQcpPmHDIMaWEa9xtY8"
ALLOWED_IMAGE_EXTENSIONS = ("png","jpg","gif","bmp")
ALLOWED_AUDIO_EXTENSIONS = ("mp3","ogg")
ALLOWED_VIDEO_EXTENSIONS = ("mp4","flv")

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
        <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
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
    def __init__(self, parent, filename = None):
        self.parent = parent
        self.file = StringIO() if filename is None else open(filename, "wb")
        self.json = filename is None
        self.len = 0
        self.result = Deferred()
    
    def dataReceived(self, data):
        self.file.write(data)
        self.len += len(data)
        if(self.len > 150*1024): # Minimum of 150KB to display. Saves flashing messages during downloading of post info or small images
            self.parent.factory.publish(self.parent.url, "{} [{:,d} KB]".format(self.parent.status, self.len / 1024))
    
    def connectionLost(self, reason):
        if reason.check(ResponseDone, PotentialDataLoss) is None: # Failed
            reason.printTraceback()
            data = []
        else:
            data = json.loads(self.file.getvalue())["response"] if self.json else []

        self.file.close()
        self.result.callback(data)

class TumblrDownloader(object):
    def __init__(self, factory, url):
        self.factory = factory
        self.url = url

        self.status = ""
        self.errored = False
        self.finished = Deferred()

        self.blog = None
        self.posts = []
        self.images = {}
        self.image_queue = []

        self.folder = safe_format("./tmp/{}", self.url)
        os.mkdir(self.folder)
        self.blog_info()
    
    def publish(self, message, error=False):
        self.status = message
        self.errored = error
        self.factory.publish(self.url, message, error)
    
    @inlineCallbacks
    def request(self, url, filename=None):
        if filename is None:
            url = safe_format("http://api.tumblr.com/v2/blog/{}/{}", self.url, url)
            url += "&api_key="+API_KEY if "?" in url else "?api_key="+API_KEY
        
        try:
            response = yield Agent(reactor, pool=self.factory.pool).request("GET", url, None, None)
        except Exception as e:
            log.err("Failed to fetch page: {}".format(url))
            returnValue([])
        else:
            deliverer = TumblrDeliverer(self, filename)
            response.deliverBody(deliverer)
            result = yield deliverer.result
            returnValue(result)
    
    @inlineCallbacks
    def blog_info(self):
        self.publish("Fetching Blog Info")
        info = yield self.request("info")
        if info:
            if info["blog"]["posts"] > 20000:
                self.publish("Archives limited to 20,000 posts for now. Send Fugi an ask to inquire about an exception.", True)
                shutil.rmtree(self.folder)
            else:
                self.blog = info["blog"]
                self.avatar_info()
        else:
            self.publish("Not a valid blog. Aborting.", True)
            shutil.rmtree(self.folder)
    
    @inlineCallbacks
    def avatar_info(self):
        self.publish("Fetching Avatar URL")
        info = yield self.request("avatar/512")
        self.blog["avatar_url"] = info["avatar_url"]

        self.publish("Fetching Avatar")
        suffix = self.blog["avatar_url"].split(".")[-1].lower().replace("jpeg","jpg")
        yield self.request(self.blog["avatar_url"].encode("UTF-8"), os.path.join(self.folder, safe_format("avatar.{}", suffix)))
        self.download_posts()
    
    @inlineCallbacks
    def download_posts(self):
        for offset in range(0, self.blog["posts"], 20):
            self.publish(safe_format("Fetching Posts ({:d}/{:d})", offset, self.blog["posts"]))
            result = yield self.request(safe_format("posts?offset={:d}", offset))
            posts = result["posts"]
            self.posts.extend(posts)
            for post in posts:
                self.parse_post(post)

        with open(safe_format("{}/posts.json", self.folder), "w") as f:
            f.write(json.dumps(self.posts))
        os.makedirs(safe_format("{}/images/", self.folder))
        self.image_queue = [x[0] for x in sorted(sorted(self.images.items(), key=lambda x: x[1]["index"]), key=lambda x: x[1]["time"])]
        self.download_images()
    
    def parse_post(self, post):
        timestamp = datetime.datetime.fromtimestamp(post["timestamp"])
        
        if post["type"] == "text":
            self.extract_images(post["body"], timestamp)
        elif post["type"] == "quote":
            self.extract_images(post["text"], timestamp)
            self.extract_images(post["source"], timestamp)
        elif post["type"] == "link":
            self.extract_images(post["description"], timestamp)
        elif post["type"] == "answer":
            self.extract_images(post["answer"], timestamp)
        elif post["type"] == "video":
            embed = ""
            width = 0
            for player in post["player"]:
                if player["width"] > width:
                    width = player["width"]
                    embed = player["embed_code"]
            post["_embed"] = embed
            self.extract_images(post["caption"], timestamp)
        elif post["type"] == "audio":
            embed = post["player"]
            self.extract_images(post["caption"].encode("UTF-8") if "caption" in post else "", timestamp)
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
                    photo["_photo"] = url
                    self.images[url] = {
                        "index": len(self.images.keys()),
                        "time": timestamp,
                        "original": url,
                        "file": url
                    }
            self.extract_images(post["caption"], timestamp)
        elif post["type"] == "chat":
            pass
        else:
            raise Exception("Invalid type - "+post["type"])
    
    def extract_images(self, body, time):
        matches = re.findall('<img([^>]*)src="([^"]*)"([^>]*)>', body, re.I)
        for match in matches:
            url = match[1]
            self.images[url] = {
                "index": len(self.images.keys()),
                "time": time,
                "original": url,
                "file": url
            }
    
    @inlineCallbacks
    def download_images(self):
        images = len(self.image_queue)
        for index, image in enumerate(self.image_queue):
            # Calculate free space
            stats = os.statvfs('/')
            free_bytes = stats.f_bavail * stats.f_frsize
            if free_bytes < 1024**3: # 1GB
                self.publish("Ran out of disk space, try again later.", True)
                shutil.rmtree(self.folder)
                return

            self.publish(safe_format("Fetching Images ({:d}/{:d})", index+1, images))
            url = image.encode("UTF-8")
            scheme = urlparse.urlparse(url, "http").scheme

            if scheme in ("http","https"):
                suffix = self.images[image]["original"].split(".")[-1].lower().replace("jpeg","jpg")
                if suffix not in ALLOWED_IMAGE_EXTENSIONS:
                    suffix = "png"
                self.images[image]["file"] = safe_format("images/image{:>06d}.{}", index+1, suffix)
                filename = safe_format("{}/{}", self.folder, self.images[image]["file"])

                yield self.request(url, filename)

            elif scheme in ("data",):
                headers = url[5:url.index(",")].split(";")
                mime = headers[0].split("/")
                data = urllib.unquote(url[1+url.index(","):])

                if mime[0] != "image":
                    print("WARNING: Non image-type mime - "+mime.join("/"))

                self.images[image]["original"] = "data."+mime[1]
                suffix = self.images[image]["original"].split(".")[-1].lower().replace("jpeg","jpg")
                if suffix not in ALLOWED_IMAGE_EXTENSIONS:
                    suffix = "png"
                self.images[image]["file"] = safe_format("images/image{:>06d}.{}", index+1, suffix)
                filename = safe_format("{}/{}", self.folder, self.images[image]["file"])

                if "base64" in headers:
                    data = base64.b64decode(data)

                with open(filename, "wb") as f:
                    f.write(data)

            else:
                del self.images[image]
                continue

        self.parse_posts()
    
    def parse_posts(self):
        self.publish("Parsing Posts")
        for number, post in enumerate(self.posts):
            timestamp = datetime.datetime.fromtimestamp(post["timestamp"])
            folder = timestamp.strftime("%Y_%m-%B")
            file = timestamp.strftime("%d_%H-%M-%S")+".html"
            data = ""
            
            if post["type"] == "text":
                title = safe_format("<h1>{}</h1>", post["title"]) if "title" in post and post["title"] else ""
                body = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["body"].encode("UTF-8"))
                data = generate_page(post, title, body)
            elif post["type"] == "quote":
                text = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["text"].encode("UTF-8"))
                source = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["source"].encode("UTF-8"))
                data = generate_page(post, "", safe_format("<blockquote><p>{}</p><small>{}</small></blockquote>", text, source))
            elif post["type"] == "link":
                description = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["description"].encode("UTF-8"))
                data = generate_page(post, safe_format("<a href='{}'><h1>{}</h1></a>", post["url"],post["title"]), description)
            elif post["type"] == "answer":
                answer = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["answer"].encode("UTF-8"))
                data = generate_page(post, safe_format("<blockquote><p>{}</p><small><a href='{}'>{}</a></small></blockquote>", post["question"],post["asking_url"],post["asking_name"]), answer)
            elif post["type"] == "video":
                embed = post["_embed"]
                caption = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["caption"].encode("UTF-8"))
                data = generate_page(post, embed, caption)
            elif post["type"] == "audio":
                embed = post["player"]
                caption = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["caption"].encode("UTF-8") if "caption" in post else "")
                title = safe_format("<h1>{} by {}</h1>", 
                    post["track_name"] if "track_name" in post else "",
                    post["artist"] if "artist" in post else ""
                )
                body = safe_format("<p><img src='{}' /></p><p style='vertical-align: middle'>{} {!s} plays</p>{}",
                    post["album_art"] if "album_art" in post else "",
                    embed,
                    post["plays"],
                    caption
                )
                data = generate_page(post, title, body)
            elif post["type"] == "photo":
                embed = ""
                for photo in post["photos"]:
                    url = photo["_photo"]
                    embed += safe_format("<img src='../{}' alt='{}' />", self.images[url]["file"], photo["caption"])
                caption = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self.patch_images, post["caption"].encode("UTF-8"))
                data = generate_page(post, embed, caption)
            elif post["type"] == "chat":
                if "dialogue" in post:
                    text = "<table>"
                    for line in post["dialogue"]:
                        text += safe_format("<tr><td style='font-weight:bold'>{}</td><td>{}</td></tr>", line["name"], line["phrase"])
                    text += "</table>"
                else:
                    text = ""
                data = generate_page(post, safe_format("<h1>{}</h1>", post["title"]), text)
            else:
                raise Exception("Invalid type - "+post["type"])
            
            if not os.path.exists(safe_format("{}/{}/", self.folder, folder)):
                os.makedirs(safe_format("{}/{}/", self.folder, folder))
            with open(safe_format("{}/{}/{}", self.folder, folder, file), "w") as f:
                f.write(data)

        self.done()

    def patch_images(self, match):
        url = match.group(2)
        if url in self.images:
            url = safe_format("../{}", self.images[url]["file"])
        return safe_format("<img{}src='{}'{}>", match.group(1), url, match.group(3))
    
    @inlineCallbacks
    def done(self):
        self.publish("Compressing")
        try:
            code = yield getProcessValue("/bin/tar", ["-cf", safe_format("./tmp/{}.tar", self.url), "-C", "./tmp/", self.url], os.environ, ".")
        except:
            self.publish("Failed to compress archive", True)
            shutil.rmtree(self.folder)
        else:
            self.cleanup()
    
    def cleanup(self):
        self.publish("Finished")
        shutil.rmtree(self.folder)
        os.rename(safe_format("./tmp/{}.tar",self.url), safe_format("./archives/{}.tar",self.url))
        os.chmod(safe_format("./archives/{}.tar",self.url), stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH)
        self.finished.callback(self.url)

class TumblrUser(Protocol):
    def __init__(self):
        self.channel = None
        self.channels = None
    
    def dataReceived(self, line):
        if self.channel:
            return
        data = json.loads(line.strip())
        self.channel = None
        if "blog" in data:
            url = data["blog"] if "." in data["blog"] else data["blog"]+".tumblr.com"
            url = url.lower().replace(" ","-")
            if "//" not in url:
                url = "//"+url
            url = urlparse.urlparse(url).hostname
            print url
            if os.path.exists(safe_format("./archives/{}.tar", url)):
                return self.done(url)
            self.channel = url
            self.factory.download(url, self)
            self.factory.subscribe(self, self.channel)
        elif "show_all" in data and data["show_all"]:
            if self.channels:
                for c in self.channels:
                    self.factory.unsubscribe(self, c)
                self.channels = None
            self.channels = self.factory.downloads.keys()
            for c in self.channels:
                if self.factory.downloads[c].errored:
                    continue
                self.factory.subscribe(self, c)
                self.messageReceived(c, self.factory.downloads[c].status)
        else:
            self.done(EMPTY_DIR)
    
    def messageReceived(self, channel, message):
        self.transport.write(json.dumps({"blog":channel,"message":message}))

    def errorReceived(self, channel, message):
        self.transport.write(json.dumps({"blog":channel,"error":message}))
    
    def done(self, url):
        if self.channel:
            self.factory.unsubscribe(self, self.channel)
            self.channel = None
        self.transport.write(json.dumps({"archive":safe_format("archives/{}.tar", url)}))
        self.transport.loseConnection()
        return url
    
    def connectionLost(self, reason=None):
        if self.channel:
            self.factory.unsubscribe(self, self.channel)
            self.channel = None
        if self.channels:
            for c in self.channels:
                self.factory.unsubscribe(self, c)
            self.channels = None

class TumblrServer(Factory):
    protocol = TumblrUser
    def __init__(self):
        self.pool = HTTPConnectionPool(reactor)
        self.pool.retryAutomatically = False
        self.pool.maxPersistentPerHost = 10
        self.pool._factory.noisy = False
        self.channels = {}
        self.downloads = {}
    
    def download(self, url, p):
        if url not in self.downloads:
            self.downloads[url] = TumblrDownloader(self, url)
            self.downloads[url].finished.addCallback(self.done)
        elif self.downloads[url].errored:
            p.errorReceived(url, self.downloads[url].status)
        else:
            p.messageReceived(url, self.downloads[url].status)
        self.downloads[url].finished.addCallback(p.done)
    
    def done(self, url):
        del self.downloads[url]
        return url
    
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
    
    def publish(self, channel, message, error=False):
        if channel in self.channels:
            for p in self.channels[channel]:
                if error:
                    p.errorReceived(channel, message)
                else:
                    p.messageReceived(channel, message)

# Start the party
shutil.rmtree("./tmp")
os.mkdir("./tmp")

index_page = File("index.html")
index_page.putChild("sockjs", SockJSResource(TumblrServer()))
index_page.putChild("archives", File("archives"))

from functools import partial
def bypass(self, path, request):
    return self
index_page.getChild = partial(bypass, index_page)

def resource():
    return index_page
