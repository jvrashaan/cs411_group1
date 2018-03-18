var request = require("request");
var eventbriteToken = 'MHPPXZ3TBMC6E47PBCYK';


module.exports = function(app, db){
    app.post('/app', (req, res) => {
	    //post method
	    res.send('Hello')
	});
};



//Example API call for team assignment 3
module.exports = function(app, db) {
    app.get('/example', (req, res) => {

      var options = { method: 'GET',
        url: 'https://www.eventbriteapi.com/v3/events/43361394097/', //event id is 43361394097 (found in url on event page)
        qs: { token: eventbriteToken }};

      request(options, function (error, response, body) {
        if (error) throw new Error(error);

        var response = body;
        response = JSON.parse(response);
        var eventName = response.name.text;
        var eventDescription = response.description.text;
        res.render('index', { title: eventName, h1: eventName, p: eventDescription});

      });
    });
	};
