<?php

require('core.php');

try {
	$oauth = new OAuth(CONSUMER_KEY,CONSUMER_SECRET);
	$oauth->setToken($_GET['oauth_token'],$_SESSION['request_secret']);

	$result = $oauth->getAccessToken('http://www.tumblr.com/oauth/access_token');
	if(!empty($result)) {
		unset($_SESSION['request_secret']);
        $_SESSION["oauth_key"] = $result['oauth_token'];
        $_SESSION["oauth_secret"] = $result['oauth_token_secret'];
        header("Location: http://tumblr-backup.fugiman.com/index.php");
	} else {
		print "Failed fetching request token, response was: " . $oauth->getLastResponse();
	}
} catch(OAuthException $E) {
	echo "Response: ". $E->lastResponse . "\n";
}


?>