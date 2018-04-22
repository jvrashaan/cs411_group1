import flask
import requests
from flask import Flask, Response, request, render_template, redirect, url_for
from flaskext.mysql import MySQL
import time
import flask.ext.login as flask_login
import json
import base64
import re
from datetime import datetime
mysql = MySQL()
app = Flask(__name__)
app.secret_key = 'still a secret'

# These will need to be changed according to your credentials, app will not run without a database
app.config['MYSQL_DATABASE_USER'] = 'root'
app.config['MYSQL_DATABASE_PASSWORD'] = '' #--------------CHANGE----------------
app.config['MYSQL_DATABASE_DB'] = 'fitbit'
app.config['MYSQL_DATABASE_HOST'] = 'localhost'
mysql.init_app(app)


#Fitbit api information
redirect_uri = "http://127.0.0.1:5000/callback"
client_id = "" # ---------------CHANGE-----------------
client_secret = "" # ---------------CHANGE-----------------

#EventBrite api information
eventbrite_token = '' # ---------------CHANGE-----------------

login_manager = flask_login.LoginManager()
login_manager.init_app(app)
conn = mysql.connect()

def getUserList():
    cursor = conn.cursor()
    cursor.execute("SELECT FBID FROM USER")
    return cursor.fetchall()

class User(flask_login.UserMixin):
    pass

@login_manager.user_loader
def user_loader(fbid):
    users = getUserList()
    if not (fbid) or fbid not in str(users):
        return

    cursor = conn.cursor()
    cursor.execute("SELECT ACCESS_TOKEN, REFRESH_TOKEN, NAME, LOCATION FROM USER WHERE FBID = '{0}'".format(fbid))
    data = cursor.fetchall()
    access_token = str(data[0][0])
    refresh_token = str(data[0][1])
    name = str(data[0][2])
    location = str(data[0][3])
    # Get a new access token if current one is expired
    if isExpired(access_token):
        new_tokens = refreshToken(fbid, access_token, refresh_token)
        access_token = new_tokens[0]
        refresh_token = new_tokens[1]

    user = User()
    user.id = fbid
    user.access_token = access_token
    user.refresh_token= refresh_token
    user.name = name
    user.location = location
    return user

@app.route('/login')
def login():
    url = "https://www.fitbit.com/oauth2/authorize?response_type=code&client_id="+ client_id + "&redirect_uri=" + redirect_uri + "&scope=activity%20location%20nutrition%20profile%20settings%20sleep%20social%20weight&expires_in=28800"
    return redirect(url)

#Gets access token once user has granted permission for the app to use Fitbit
@app.route('/callback', methods=['POST', 'GET'])
def callback():

    auth_header = client_id + ":" + client_secret
    encoded_auth_header = str((base64.b64encode(auth_header.encode())).decode('utf-8'))

    code = request.url.split("=")[1]
    url = "https://api.fitbit.com/oauth2/token"
    querystring = {"grant_type":"authorization_code","redirect_uri":redirect_uri,"clientId":client_id,"code": code}
    headers = {'Authorization': 'Basic '+ encoded_auth_header, 'Content-Type': "application/x-www-form-urlencoded"}
    response = requests.request("POST", url, headers=headers, params=querystring)
    response = json.loads(response.text)

    #Get the user's Fitbit id
    fbid = response['user_id']
    #Get access token to use Fitbit api
    access_token = response['access_token']
    #Get refresh token to refresh the access token once it expires
    refresh_token = response['refresh_token']

    users = getUserList()
    #If user hasn't logged into our app before
    if fbid not in str(users):
        registerUser(fbid, access_token, refresh_token)
        return flask.redirect(flask.url_for('register'))

    insertAccessToken(fbid, access_token)
    insertRefreshToken(fbid, refresh_token)
    user_name =  getUserName(fbid, access_token)

    #Create a user instance and log the user in
    user = User()
    user.id = fbid
    flask_login.login_user(user)

    return flask.redirect(flask.url_for('protected'))  # protected is a function defined in profile route


# If user has never logged into our app before
@app.route('/register', methods = ['POST', 'GET'])
@flask_login.login_required
def register():
    if flask.request.method == 'GET':
        return render_template('register.html')
    else:
        location=request.form.get('location') #or users could enter their location on search page, leaving it here as an example
        cursor = conn.cursor()
        cursor.execute("UPDATE USER SET LOCATION = '{0}' WHERE FBID = '{1}'".format(location,flask_login.current_user.id))

        return redirect(flask.url_for('protected'))

#user profile route
@app.route('/profile')
@flask_login.login_required
def protected():
    activities = getActivities()
    events = recommendEvents()
    return render_template('profile.html', name=flask_login.current_user.name, activities = activities, events = events)

#search events
#@flask_login.login_required
@app.route("/searchEvents", methods=['POST'])
def searchEventsRoute():
    cursor = conn.cursor()
    #check results cache for term given.
    searchterm = flask.request.form['search_term']
    cursor.execute("SELECT NAME, DATE, VENUE, DES, LINK, RNUM FROM RESULTCACHE WHERE SID = '{0}'".format(searchterm))
    result = cursor.fetchall()
    if(result):
        #results found, return them.
        #print("CACHE PULL")
        return render_template('searchEvents2.html', results = result, name= flask_login.current_user.name, message="Here Are Your Search Results!")

    else:
        #get first instance of search results.
        results = searchEvents(flask.request.form['search_term'], flask_login.current_user.location)
        results = [{"name":results[i][0], "date":reformatDate(results[i][1]), "venue":results[i][2], "desc":results[i][3], "link":results[i][4], "activity":results[i][5], "resNum": i} for i in range(len(results))]
        #insert search results into the cache.
        #print("api call")
        for result in results:
            cursor.execute("INSERT INTO RESULTCACHE (SID, NAME, DATE, VENUE, DES, LINK, RNUM) VALUES ('{0}', '{1}', '{2}', '{3}', '{4}', '{5}', '{6}')".format(searchterm, result["name"], result["date"], result["venue"], result["desc"], result["link"], result["resNum"]))
        conn.commit()
        #delete old results
        deleteOldResults()
        return render_template('searchEvents.html', results= results, name= flask_login.current_user.name, message="Here Are Your Search Results!")

#helper function
def searchcount():
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT SID) FROM RESULTCACHE")
    count = cursor.fetchall()[0][0]
    return count

#deletes old results from results cache if more than 5 searches have occured.
def deleteOldResults():
    count = searchcount()
    if(count > 5):
        #get SID of first record in cache.
        cursor = conn.cursor()
        cursor.execute("SELECT SID FROM RESULTCACHE ORDER BY ID LIMIT 1")
        sid = cursor.fetchall()[0][0]
        #remove all results matching sid
        cursor.execute("DELETE FROM RESULTCACHE WHERE SID = '{0}'".format(sid))
        conn.commit()

#search events api call
def searchEvents(search_term, location):
    url = "https://www.eventbriteapi.com/v3/events/search/"
    head = {'Authorization': 'Bearer {}'.format(eventbrite_token)}
    data = {"q": search_term, "sort_by": "date", "location.address": location, "categories":"108", "expand": "venue" } #108 is fitness category
    # city = flask.request.form['city']   ######    needs to be done later
    myResponse = requests.get(url, headers = head, params=data)
    results = []
    if(myResponse.ok):
        jData = json.loads(myResponse.text)
        events = jData['events']
        for event in events:
            #format the strings for the database
            name = event['name']['text']
            name = list(name)
            for j in range(len(name)):
                if(name[j] == "'"):
                    name[j] = "''"
            name = "".join(name)

            date = event['start']['local']

            venue = event['venue']['address']['address_1']
            if venue == None:
                venue = "Venue in description."

            desc = event['description']['text']
            if desc == None:
                desc = "No description provided."
            else:
                desc = list(desc)
                for i in range(len(desc)):
                    if(desc[i] == "'"):
                        desc[i] = "''"
                desc = "".join(desc)

            eventbrite_link = event['url']
            activity = search_term #the fitbit activity that this event is matched with

            #results.append({"name":name, "desc": desc, "time":time, "venue":venue, "activity": activity})
            results.append((name, date, venue, desc, eventbrite_link, activity))
    else:
        # If response code is not ok (200), print the resulting http error code with description
        myResponse.raise_for_status()

    return results

# Dates from EventBrite api are in the format '2018-04-21T13:00:00'. reformatDate() turns it into
# 'April 21 at 1:00PM'
def reformatDate(date):
    new_date = datetime.strptime(date, '%Y-%m-%dT%H:%M:%S')
    new_date = new_date.strftime("%B %-d at %-I:%M%p")
    return new_date


@app.route("/", methods=['GET'])
def hello():
    if flask_login.current_user.is_authenticated:
        return render_template('homepage.html', name=flask_login.current_user.name)
    else:
        return render_template('homepage.html')

@app.route('/logout')
def logout():
    flask_login.logout_user()
    return render_template('homepage.html', message='Logged out')

#Get username and store it in database
def getUserName(fbid, access_token):
    url = "https://api.fitbit.com/1/user/"+ fbid +"/profile.json"
    headers = {'Authorization': "Bearer " + access_token}
    response = requests.request("GET", url, headers=headers)
    response = json.loads(response.text)
    user_name = response['user']['displayName']
    cursor = conn.cursor()
    cursor.execute("UPDATE USER SET NAME = '{0}' WHERE FBID = '{1}'".format(user_name, fbid))

    return user_name

#save user events
@app.route('/saveEvent', methods=["POST"])
@flask_login.login_required
def saveEvent():
    cursor = conn.cursor()
    resnum = flask.request.form["name"]
    cursor.execute("SELECT SID, NAME, DATE, VENUE, DES, LINK FROM RESULTCACHE WHERE RNUM = '{0}'".format(resnum))
    event = cursor.fetchall()[0]
    #check if saved event already exists
    cursor.execute("SELECT LINK FROM SAVEDEVENTS WHERE LINK = '{0}'".format(event[5]))
    if (cursor.fetchall()):
        return render_template('savedEvents.html', message="Event already saved", events=getSearchSaved(),
                               name=flask_login.current_user.name)
    # reformat strings to avoid database errors.
    else:
        tempN = event[1]
        tempN = list(tempN)
        for i in range(len(tempN)):
            if (tempN[i] == "'"):
                tempN[i] = "''"
        tempN = "".join(tempN)

        tempD = event[4]
        tempD = list(tempD)
        for i in range(len(tempD)):
            if (tempD[i] == "'"):
                tempD[i] = "''"
        tempD = "".join(tempD)
        fbid = flask_login.current_user.id
        cursor.execute("INSERT INTO SAVEDEVENTS (FBID, SID, NAME, DATE, VENUE, DES, LINK) VALUES ('{0}', '{1}', '{2}', '{3}', '{4}', '{5}', '{6}')".format(fbid, event[0], tempN, event[2], event[3], tempD, event[5]))
        conn.commit()
        return render_template('savedEvents.html', events=getSearchSaved())

#get user saved events
@app.route('/savedEvents', methods=["GET"])
@flask_login.login_required
def getSavedEvents():
    fbid = flask_login.current_user.id
    cursor = conn.cursor()
    cursor.execute("SELECT NAME, DATE, VENUE, DES, LINK FROM SAVEDEVENTS WHERE FBID = '{0}'".format(fbid))
    event = cursor.fetchall()
    return render_template('savedEvents.html', events= event)

#helper function for search events saved
@flask_login.login_required
def getSearchSaved():
    fbid = flask_login.current_user.id
    cursor = conn.cursor()
    cursor.execute("SELECT NAME, DATE, VENUE, DES, LINK FROM SAVEDEVENTS WHERE FBID = '{0}'".format(fbid))
    event = cursor.fetchall()
    return event

#get fitbit activities
def getActivities():
    url = "https://api.fitbit.com/1/user/"+ flask_login.current_user.id +"/activities/list.json?afterDate=2005-01-01&sort=desc&limit=20&offset=0"
    headers = {'Authorization': "Bearer " + flask_login.current_user.access_token}
    response = requests.request("GET", url, headers=headers)
    # print (response.headers)
    response = json.loads(response.text)

    activities = []

    cursor = conn.cursor()
    for activity in response['activities']:
        activities.append(activity['activityName'])
        cursor.execute("INSERT INTO ACTIVITIES (FBID, ACTIVITY) VALUES ('{0}', '{1}') ON DUPLICATE KEY UPDATE ACTIVITY=ACTIVITY;".format(flask_login.current_user.id, activity['activityName']))
    conn.commit()
    return activities

def recommendEvents():
    #clear the current table.
    emptyRecommendations()

    #fill the table with new events
    cursor = conn.cursor()
    cursor.execute("SELECT FBID, ACTIVITY FROM ACTIVITIES WHERE FBID = '{0}'".format(flask_login.current_user.id))
    activities = cursor.fetchall()
    events = []
    #call searchEvents() with each of the user's activities
    for index in range(len(activities)):
        activity = str(activities[index][1])
        event = searchEvents(activity, flask_login.current_user.location)
        events.append(event)

    #flatten the 2D array into a 1D array
    events = [event for category in events for event in category]
    #sort events by date
    sorted_events = sorted(events, key=lambda x: x[1])

    #Put date in readable format
    sorted_events = [{"name":sorted_events[i][0], "date":reformatDate(sorted_events[i][1]), "venue":sorted_events[i][2], "desc":sorted_events[i][3], "link":sorted_events[i][4], "activity":sorted_events[i][5], "resNum": i} for i in range(len(sorted_events))]
    #insert events into table
    for event in sorted_events:
        cursor.execute("INSERT INTO RECOMMENDATIONS (NAME, DATE, VENUE, DES, LINK, RNUM) VALUES ('{0}', '{1}', '{2}', '{3}', '{4}', '{5}')".format(event["name"], event["date"], event["venue"], event["desc"], event["link"], event["resNum"]))
    conn.commit()
    return sorted_events

def emptyRecommendations():
    cursor = conn.cursor()
    cursor.execute("DELETE FROM RECOMMENDATIONS")
    conn.commit()

#save user events
@app.route('/saveEventRecommendations', methods=["POST"])
@flask_login.login_required
def saveEventRecommendations():
    cursor = conn.cursor()
    resnum = flask.request.form["name"]
    cursor.execute("SELECT NAME, DATE, VENUE, DES, LINK FROM RECOMMENDATIONS WHERE RNUM = '{0}'".format(resnum))
    event = cursor.fetchall()[0]
    #check if saved event is already in table
    cursor.execute("SELECT LINK FROM SAVEDEVENTS WHERE LINK = '{0}'".format(event[4]))
    if(cursor.fetchall()):
        return render_template('profile.html', message= "Event already saved", events=recommendEvents(), name= flask_login.current_user.name, activities = getActivities())
    #reformat strings to avoid database errors.
    else:
        tempN = event[0]
        tempN = list(tempN)
        for i in range(len(tempN)):
            if (tempN[i] == "'"):
                tempN[i] = "''"
        tempN = "".join(tempN)

        tempD = event[3]
        tempD = list(tempD)
        for i in range(len(tempD)):
            if (tempD[i] == "'"):
                tempD[i] = "''"
        tempD = "".join(tempD)

        fbid = flask_login.current_user.id
        cursor.execute(
            "INSERT INTO SAVEDEVENTS (FBID, NAME, DATE, VENUE, DES, LINK) VALUES ('{0}', '{1}', '{2}', '{3}', '{4}', '{5}')".format(
                fbid, tempN, event[1], event[2], tempD, event[4]))
        conn.commit()
        return render_template('savedEvents.html', events=getRecSaved())

#helper function for recommendations saved
@flask_login.login_required
def getRecSaved():
    fbid = flask_login.current_user.id
    cursor = conn.cursor()
    cursor.execute("SELECT NAME, DATE, VENUE, DES, LINK FROM SAVEDEVENTS WHERE FBID = '{0}'".format(fbid))
    event = cursor.fetchall()
    return event

@login_manager.unauthorized_handler
def unauthorized_handler():
    return render_template('unauth.html')

def registerUser(fbid, access_token, refresh_token):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO USER (FBID) VALUES ('{0}')".format(fbid))
    conn.commit()
    insertAccessToken(fbid,access_token)
    insertRefreshToken(fbid, refresh_token)
    user_name = getUserName(fbid, access_token)

    #Create a user instance and log the user in
    user = User()
    user.id = fbid
    flask_login.login_user(user)


def insertAccessToken(fbid, access_token):
    cursor = conn.cursor()
    cursor.execute("UPDATE USER SET ACCESS_TOKEN = '{0}' WHERE FBID = '{1}'".format(access_token, fbid))
    conn.commit()

def insertRefreshToken(fbid, refresh_token):
    cursor = conn.cursor()
    cursor.execute("UPDATE USER SET REFRESH_TOKEN = '{0}' WHERE FBID = '{1}'".format(refresh_token, fbid))
    conn.commit()

#Checks state of current access token by making a call to the Fitbit api
def isExpired(access_token):
    headers = {
    'accept': 'application/json',
    'content-type': 'application/x-www-form-urlencoded',
    'Authorization': 'Bearer ' + access_token,
    }
    data = [
      ('token', access_token),
    ]
    response = requests.post('https://api.fitbit.com/oauth2/introspect', headers=headers, data=data)
    response = str(response.content.decode("utf-8"))
    response = re.sub('^[^{]*', '', response)
    response = json.loads(response)

    if 'active' not in response:
        return True
    else:
        return False

#Refresh the access token and store new access token and refresh token in database
def refreshToken(fbid, access_token, refresh_token):

    auth_header = client_id + ":" + client_secret
    encoded_auth_header = str((base64.b64encode(auth_header.encode())).decode('utf-8'))

    url = "https://api.fitbit.com/oauth2/token"
    querystring = {"grant_type":"refresh_token","refresh_token": refresh_token, "expires_in": 28800}
    headers = {'Authorization': 'Basic '+ encoded_auth_header, 'Content-Type': "application/x-www-form-urlencoded"}

    response = requests.request("POST", url, headers=headers, params=querystring)
    response = json.loads(response.text)
    print(response)
    access_token = response['access_token']
    refresh_token = response['refresh_token']

    insertAccessToken(fbid, access_token)
    insertRefreshToken(fbid, refresh_token)

    return [access_token, refresh_token]


if __name__ == '__main__':
    app.run(port=5000, debug=True)
