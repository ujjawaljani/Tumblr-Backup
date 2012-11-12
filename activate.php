<?php

require('core.php');

try {
	$oauth = new OAuth(CONSUMER_KEY,CONSUMER_SECRET);

	$result = $oauth->getRequestToken('http://www.tumblr.com/oauth/request_token','http://tumblr-backup.fugiman.com/new.php');
	if(!empty($result)) {
		$_SESSION['request_secret'] = $result['oauth_token_secret'];
		header('Location: http://www.tumblr.com/oauth/authorize?oauth_token=' . $result['oauth_token']);
	} else {
		print "Failed fetching request token, response was: " . $oauth->getLastResponse();
	}
} catch(OAuthException $E) {
	echo "Response: ". $E->lastResponse . "\n";
}

?>