# -*- coding: utf-8 -*-
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol, ProcessProtocol, Factory
from twisted.internet.error import ProcessTerminated
from twisted.web.client import HTTPConnectionPool, Agent, ResponseDone
from twisted.web.http import PotentialDataLoss
from twisted.web.http_headers import Headers
from txsockjs.factory import SockJSFactory
import json, time, urllib, urlparse, hashlib, hmac, binascii, random, os, datetime, shutil, zipfile, re, locale, base64

API_KEY = "RfTll0TPYGVm3kTbauZRH5QVAgBH3UAkQcpPmHDIMaWEa9xtY8"
ALLOWED_IMAGE_EXTENSIONS = ("png","jpg","gif","bmp")
ALLOWED_AUDIO_EXTENSIONS = ("mp3","ogg")
ALLOWED_VIDEO_EXTENSIONS = ("mp4","flv")

locale.setlocale(locale.LC_ALL, '')


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

def url2file(url):
    return hashlib.sha256(url.replace("/","|").replace(" ","-")).hexdigest()

class TumblrProcess(ProcessProtocol):
    def __init__(self, parent, callback):
        self.parent = parent
        self.callback = callback
        self.percentage = ""
    
    def outReceived(self, data):
        index = data.rfind("%")
        if index >= 0:
            percentage = data[index-3:index+1].strip()
            if percentage != self.percentage:
                self.percentage = percentage
                self.parent.factory.publish(self.parent.url, self.parent.status + " [" + self.percentage + "]")
    
    def processEnded(self, status=None):
        if status and status.check(ProcessTerminated) is not None:
            status.printTraceback()
        else:
            self.callback()

class TumblrDeliverer(Protocol):
    def __init__(self, parent, callback, json, url):
        self.parent = parent
        self.callback = callback
        self.json = json
        self.url = url
        self.buf = "";
    
    def dataReceived(self, data):
        self.buf += data
        kb = len(self.buf)/1024
        if(kb > 149): # Minimum of 150KB to display. Saves flashing messages during downloading of post info or small images
            self.parent.factory.publish(self.parent.url, self.parent.status + " [" + locale.format("%d", kb, grouping=True) + " KB]")
    
    def connectionLost(self, reason):
        if reason.check(ResponseDone, PotentialDataLoss) is None: # Failed
            reason.printTraceback()
            data = [] if self.json else ""
        else:
            data = json.loads(self.buf)["response"] if self.json else self.buf
            if not self.json and data:
                with open("/mnt/cache/"+url2file(self.url), "wb") as f:
                    f.write(data)
        self.callback(data)

class TumblrDownloader(object):
    def __init__(self, factory, url, images, audio, video):
        self.factory = factory
        self.url = url
        self.dl_images = images;
        self.dl_audio = audio;
        self.dl_video = video;
        self.blog = None
        self.status = ""
        self.finished = Deferred()
        self.posts = []
        self.images = {}
        self.audios = {}
        self.videos = {}
        self.image_queue = []
        self.audio_queue = []
        self.video_queue = []
        self.image = 0
        self.audio = 0
        self.video = 0
        self.folder = safe_format("/mnt/tmp/{}", self.url)
        os.mkdir(self.folder)
        self._request("info", self.blog_info)
        self.publish("Fetching Blog Info")
    
    def publish(self, message):
        self.status = message
        self.factory.publish(self.url, message)
    
    def _request(self, suffix, callback, raw=False):
        if raw:
            url = suffix
        else:
            url = safe_format("http://api.tumblr.com/v2/blog/{}/{}", self.url, suffix)
            url += "&api_key="+API_KEY if "?" in url else "?api_key="+API_KEY
        
        if raw and os.path.exists("/mnt/cache/"+url2file(url)):
            with open("/mnt/cache/"+url2file(url),"rb") as f:
                callback(f.read())
            return
        
        d = Agent(reactor, pool=self.factory.pool).request("GET", url, None, None)
        d.addCallback(self._deliver, callback, not raw, url)
        d.addErrback(self._error, callback, not raw)
    
    def _error(self, failure, callback, json):
        failure.printTraceback()
        callback([] if json else "")
    
    def _deliver(self, response, callback, json, url):
        response.deliverBody(TumblrDeliverer(self, callback, json, url))
    
    def blog_info(self, info):
        if info:
            self.blog = info["blog"]
            self._request("avatar/512", self.avatar_info)
            self.publish("Fetching Avatar URL")
        else:
            self.publish("Not a valid blog. Aborting.")
            shutil.rmtree(self.folder)
    
    def avatar_info(self, info):
        self.blog["avatar_url"] = info["avatar_url"]
        self._request(self.blog["avatar_url"].encode("UTF-8"), self.avatar, True)
        self.publish("Fetching Avatar")
    
    def avatar(self, avatar):
        suffix = self.blog["avatar_url"].split(".")[-1].lower().replace("jpeg","jpg")
        with open(safe_format("{}/avatar.{}", self.folder, suffix), "wb") as f:
            f.write(avatar)
        self.download_posts()
    
    def download_posts(self):
        post = len(self.posts)
        self._request(safe_format("posts?offset={!s}", post), self.post_data)
        self.publish(safe_format("Fetching Posts ({!s}/{!s})", post, self.blog["posts"]))
    
    def post_data(self, posts):
        posts = posts["posts"]
        self.posts.extend(posts)
        for post in posts:
            timestamp = datetime.datetime.fromtimestamp(post["timestamp"])
            
            if post["type"] == "text":
                self._extract_images(post["body"], timestamp)
            elif post["type"] == "quote":
                self._extract_images(post["text"], timestamp)
                self._extract_images(post["source"], timestamp)
            elif post["type"] == "link":
                self._extract_images(post["description"], timestamp)
            elif post["type"] == "answer":
                self._extract_images(post["answer"], timestamp)
            elif post["type"] == "video":
                embed = ""
                width = 0
                for player in post["player"]:
                    if player["width"] > width:
                        width = player["width"]
                        embed = player["embed_code"]
                post["_embed"] = embed
                post["_video"] = self._extract_video(embed, timestamp)
                self._extract_images(post["caption"], timestamp)
            elif post["type"] == "audio":
                embed = post["player"]
                post["_audio"] = self._extract_audio(embed, timestamp)
                self._extract_images(post["caption"].encode("UTF-8") if "caption" in post else "", timestamp)
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
                self._extract_images(post["caption"], timestamp)
            elif post["type"] == "chat":
                pass
            else:
                raise Exception("Invalid type - "+post["type"])
        if len(self.posts) >= self.blog["posts"]:
            with open(safe_format("{}/posts.json", self.folder), "w") as f:
                f.write(json.dumps(self.posts))
            os.makedirs(safe_format("{}/images/", self.folder))
            os.makedirs(safe_format("{}/audio/", self.folder))
            os.makedirs(safe_format("{}/video/", self.folder))
            self.image_queue = [x[0] for x in sorted(sorted(self.images.items(), key=lambda x: x[1]["index"]), key=lambda x: x[1]["time"])]
            self.audio_queue = [x[0] for x in sorted(sorted(self.audios.items(), key=lambda x: x[1]["index"]), key=lambda x: x[1]["time"])]
            self.video_queue = [x[0] for x in sorted(sorted(self.videos.items(), key=lambda x: x[1]["index"]), key=lambda x: x[1]["time"])]
            reactor.callLater(0, self.download_images)
        else:
            reactor.callLater(0, self.download_posts)
    
    def _patch_images(self, match):
        url = match.group(2)
        if url in self.images:
            url = safe_format("../{}", self.images[url]["file"])
        return safe_format("<img{}src='{}'{}>", match.group(1), url, match.group(3))
    
    def _extract_images(self, body, time):
        if not self.dl_images:
            return None
        matches = re.findall('<img([^>]*)src="([^"]*)"([^>]*)>', body, re.I)
        for match in matches:
            url = match[1]
            self.images[url] = {
                "index": len(self.images.keys()),
                "time": time,
                "original": url,
                "file": url
            }
    
    def _extract_audio(self, body, time):
        if not self.dl_audio:
            return None
        match = re.search('audio_file=([^&]*)&', body, re.I)
        if match is None:
            return None
        url = match.group(1)
        self.audios[url] = {
            "index": len(self.audios.keys()),
            "time": time,
            "original": url,
            "file": url
        }
        return url
    
    def _extract_video(self, body, time):
        if not self.dl_video:
            return None
        match = re.search('<iframe([^>]*)src="([^"]*)"([^>]*)>', body, re.I)
        if match is None:
            return None
        url = urlparse.urlparse(match.group(2))
        if "tumblr" in url.hostname: # Tumblr
            url = safe_format("tumblr:{}", url.path)
        elif "youtube" in url.hostname: # Youtube
            id = url.path.strip("/").split("/")[1]
            url = safe_format("yt:{}", id)
        else:
            return None
        self.videos[url] = {
            "index": len(self.videos.keys()),
            "time": time,
            "original": url,
            "file": url
        }
        return url
    
    def download_images(self):
        if self.image >= len(self.image_queue):
            return self.download_audios()
        url = self.image_queue[self.image].encode("UTF-8")
        scheme = urlparse.urlparse(url, "http").scheme
        if scheme in ("http","https"):
            self._request(url, self.image_data, True)
        elif scheme in ("data",):
            try:
                mime = url[5:url.index(";")].split("/")
                data = url[1+url.index(","):]
                if mime[0] != "image":
                    print("WARNING: Non image-type mime - "+mime.join("/"))
                self.images[self.image_queue[self.image]]["original"] = "data."+mime[1]
                self.image_data(base64.b64decode(data))
            except:
                print safe_format("WTF data uri: {}", url)
                del self.images[self.image_queue[self.image]]
                self.image += 1
                reactor.callLater(0, self.download_images)
        else:
            del self.images[self.image_queue[self.image]]
            self.image += 1
            reactor.callLater(0, self.download_images)
        self.publish(safe_format("Fetching Images ({!s}/{!s})", self.image+1, len(self.image_queue)))
    
    def image_data(self, image):
        suffix = self.images[self.image_queue[self.image]]["original"].split(".")[-1].lower().replace("jpeg","jpg")
        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
            suffix = "png"
        self.images[self.image_queue[self.image]]["file"] = safe_format("images/image{:>06d}.{}", self.image+1, suffix)
        with open(safe_format("{}/{}", self.folder, self.images[self.image_queue[self.image]]["file"]), "wb") as f:
            f.write(image)
        self.image += 1
        reactor.callLater(0, self.download_images)
    
    def download_audios(self):
        if self.audio >= len(self.audio_queue):
            return self.download_videos()
        url = safe_format("{}?plead=please-dont-download-this-or-our-lawyers-wont-let-us-host-audio", self.audio_queue[self.audio])
        d = Agent(reactor, pool=self.factory.pool).request("HEAD", url, None, None)
        d.addCallback(self.audio_info)
        d.addErrback(self._error, self.audio_info, False)
        self.publish(safe_format("Fetching Audio ({!s}/{!s})", self.audio+1, len(self.audio_queue)))
    
    def audio_info(self, response):
        if response:
            url = response.headers.getRawHeaders("Location")[0]
            self.audios[self.audio_queue[self.audio]]["original"] = url
            self._request(url, self.audio_data, True)
        else:
            del self.audios[self.audio_queue[self.audio]]
            self.audio += 1
            reactor.callLater(0, self.download_audios)
    
    def audio_data(self, audio):
        suffix = self.audios[self.audio_queue[self.audio]]["original"].split(".")[-1].lower()
        if suffix not in ALLOWED_AUDIO_EXTENSIONS:
            suffix = "mp3"
        self.audios[self.audio_queue[self.audio]]["file"] = safe_format("audio/audio{:>06d}.{}", self.audio+1, suffix)
        with open(safe_format("{}/{}", self.folder, self.audios[self.audio_queue[self.audio]]["file"]), "wb") as f:
            f.write(audio)
        self.audio += 1
        reactor.callLater(0, self.download_audios)
    
    def download_videos(self):
        if self.video >= len(self.video_queue):
            reactor.callLater(0, self.parse_posts)
            self.publish("Parsing Posts")
            return
        type, chaff, param = self.video_queue[self.video].partition(":")
        method = getattr(self, safe_format("download_videos_{}", type), None)
        if method is None:
            del self.videos[self.video_queue[self.video]]
            self.video += 1
            self.download_videos()
        else:
            method(param)
            self.publish(safe_format("Fetching Video ({!s}/{!s})", self.video+1, len(self.video_queue)))
    
    def video_fail(self, chaff):
        del self.videos[self.video_queue[self.video]]
        self.video += 1
        self.download_videos()
    
    def download_videos_tumblr(self, path):
        url = safe_format("http://www.tumblr.com{}", path)
        self._request(url.encode("UTF-8"), self.video_tumblr_embed, True)
    
    def video_tumblr_embed(self, html):
        match = re.search('<source([^>]*)src="([^"]*)"([^>]*)>', html, re.I)
        if match is None:
            del self.videos[self.video_queue[self.video]]
            self.video += 1
            self.download_videos()
        else:
            url = match.group(2)
            d = Agent(reactor, pool=self.factory.pool).request("HEAD", url, None, None)
            d.addCallback(self.video_tumblr_info)
            d.addErrback(self._error, self.video_fail, False)
    
    def video_tumblr_info(self, response):
        url = response.headers.getRawHeaders("Location")[0]
        self.videos[self.video_queue[self.video]]["original"] = url
        self._request(url, self.video_data, True)
    
    def download_videos_yt(self, id):
        url = safe_format("http://www.youtube.com/watch?v={}", id)
        self._request(url.encode("UTF-8"), self.video_yt_info, True)
    
    def video_yt_info(self, html):
        begin = html.find("yt.playerConfig = {")
        if begin < 0:
            del self.videos[self.video_queue[self.video]]
            if html.find("This video is unavailable") < 0:
                print "Error downloading video #"+str(self.video)
                with open("/mnt/tmp/tumblr_server_error.log","a") as f:
                    blogstr = "===== BLOG: "+self.url+" ====="
                    videostr = "===== VIDEO: "+self.video_queue[self.video]+" ====="
                    spacer = "="*max(len(blogstr), len(videostr))
                    f.write(safe_format("\n\n{}\n{}\n{}\n{}\n\n{}", spacer, blogstr, videostr, spacer, html))
            self.video += 1
            self.download_videos()
        else:
            end = html.find("};", begin)
            data = html[begin+18:end+1]
            params = json.loads(data)
            streams = urlparse.parse_qs(params["args"]["url_encoded_fmt_stream_map"])
            index = -1
            for i in range(len(streams["url"])):
                quality = streams["quality"][i].split(",")[0]
                type = streams["type"][i].split(";")[0].split("/")[1]
                if type == "mp4" and quality in ("hd720","large","medium","small"):
                    index = i
                    break
            try:
                url = safe_format("{}&signature={}", streams["url"][index], streams["sig"][index])
            except:
                print "Error building youtube url: "+repr(streams)
                del self.videos[self.video_queue[self.video]]
                self.video += 1
                self.download_videos()
                return
            if index >= 0:
                self.videos[self.video_queue[self.video]]["original"] = "youtube.mp4"
            else:
                self.videos[self.video_queue[self.video]]["original"] = "youtube.flv"
            self._request(url, self.video_data, True)
    
    def video_data(self, video):
        suffix = self.videos[self.video_queue[self.video]]["original"].split(".")[-1].lower()
        if suffix not in ALLOWED_VIDEO_EXTENSIONS:
            suffix = "mp4"
        self.videos[self.video_queue[self.video]]["file"] = safe_format("video/video{:>06d}.{}", self.video+1, suffix)
        with open(safe_format("{}/{}", self.folder, self.videos[self.video_queue[self.video]]["file"]), "wb") as f:
            f.write(video)
        self.video += 1
        reactor.callLater(0, self.download_videos)
    
    def parse_posts(self):
        for number, post in enumerate(self.posts):
            timestamp = datetime.datetime.fromtimestamp(post["timestamp"])
            folder = timestamp.strftime("%Y_%m-%B")
            file = timestamp.strftime("%d_%H-%M-%S")+".html"
            data = ""
            
            if post["type"] == "text":
                title = safe_format("<h1>{}</h1>", post["title"]) if "title" in post and post["title"] else ""
                body = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["body"].encode("UTF-8"))
                data = generate_page(post, title, body)
            elif post["type"] == "quote":
                text = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["text"].encode("UTF-8"))
                source = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["source"].encode("UTF-8"))
                data = generate_page(post, "", safe_format("<blockquote><p>{}</p><small>{}</small></blockquote>", text, source))
            elif post["type"] == "link":
                description = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["description"].encode("UTF-8"))
                data = generate_page(post, safe_format("<a href='{}'><h1>{}</h1></a>", post["url"],post["title"]), description)
            elif post["type"] == "answer":
                answer = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["answer"].encode("UTF-8"))
                data = generate_page(post, safe_format("<blockquote><p>{}</p><small><a href='{}'>{}</a></small></blockquote>", post["question"],post["asking_url"],post["asking_name"]), answer)
            elif post["type"] == "video":
                if post["_video"] in self.videos:
                    embed = safe_format("<video src='../{}' controls><a href='../{}'>Watch video</a></video>", self.videos[post["_video"]]["file"], self.videos[post["_video"]]["file"])
                else:
                    embed = post["_embed"]
                caption = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["caption"].encode("UTF-8"))
                data = generate_page(post, embed, caption)
            elif post["type"] == "audio":
                if post["_audio"] in self.audios:
                    embed = safe_format("<audio src='../{}' controls><a href='../{}'>Listen to audio</a></audio>", self.audios[post["_audio"]]["file"], self.audios[post["_audio"]]["file"])
                else:
                    embed = post["player"]
                caption = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["caption"].encode("UTF-8") if "caption" in post else "")
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
                caption = re.sub('<img([^>]*)src="([^"]*)"([^>]*)>', self._patch_images, post["caption"].encode("UTF-8"))
                data = generate_page(post, embed, caption)
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
        reactor.callLater(0, self.done)
    
    def done(self):
        self.publish("Compressing")
        reactor.spawnProcess(TumblrProcess(self, self.cleanup), "/usr/local/bin/7z", ["7z","a","-mx=1","-tzip",safe_format("/mnt/tmp/{}.zip",self.url),self.folder], os.environ, "/mnt", usePTY=True)
    
    def cleanup(self):
        self.publish("Finished")
        shutil.rmtree(self.folder)
        os.rename(safe_format("/mnt/tmp/{}.zip",self.url), safe_format("/mnt/archives/{}.zip",self.url))
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
            self.channel = url
            self.factory.download(url, self, data["images"], data["audio"], data["video"])
            self.factory.subscribe(self, self.channel)
        elif "show_all" in data and data["show_all"]:
            if self.channels:
                for c in self.channels:
                    self.factory.unsubscribe(self, c)
                self.channels = None
            self.channels = self.factory.downloads.keys()
            for c in self.channels:
                self.factory.subscribe(self, c)
                self.messageReceived(c, self.factory.downloads[c].status)
        else:
            self.done(EMPTY_DIR)
    
    def messageReceived(self, channel, message):
        self.transport.write(json.dumps({"blog":channel,"message":message}))
    
    def done(self, url):
        if self.channel:
            self.factory.unsubscribe(self, self.channel)
            self.channel = None
        self.transport.write(json.dumps({"archive":safe_format("archives/{}.zip", url)}))
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
        self.pool.retryAutomatically = True
        self.pool.maxPersistentPerHost = 10
        self.channels = {}
        self.downloads = {}
    
    def download(self, url, p, images, audio, video):
        if url not in self.downloads:
            self.downloads[url] = TumblrDownloader(self, url, images, audio, video)
            self.downloads[url].finished.addCallback(self.done)
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
    
    def publish(self, channel, message):
        if channel in self.channels:
            for p in self.channels[channel]:
                p.messageReceived(channel, message)

# Start the party
shutil.rmtree("/mnt/tmp")
os.mkdir("/mnt/tmp")
tumblr = TumblrServer()
reactor.listenTCP(8080, SockJSFactory(tumblr))
reactor.run()