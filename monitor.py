#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Nov 29 11:55:19 2018

@author: marinmerlin
"""

import argparse
import time
from threading import Thread, RLock, Event
import curses
from curses.textpad import Textbox
import requests
import validators
import os
import uuid

#Function that returns the averaga metrics given a metrics list and the time interval on which to calculate the average
def getAverage(interval, data, key):
    
    #The average metrics of all monitored sites are stored in a global variable
    global websitesAverage
    averageMetrics = {
        "availibility": 0,
        "response_time": 0,
        "max_response_time": 0,
        "status_code_count": {},
        "size": 0
    }
    #We will be iterating over the data from the end of the list, and we stop when the time of the current metric is more than "interval" long ago
    index = len(data) - 1
    if index >= 0:
        while index >= 0 and data[index]["time"] > time.time() - interval:
            
            #Here we calculate all the average metrics
            #First is the site down
            if(data[index]["response"]):
                averageMetrics["availibility"] += 1
            
            #The average response time
            averageMetrics["response_time"] += data[index]["response_time"]
            
            #The max response time
            if(data[index]["response_time"] > averageMetrics["max_response_time"]):
                averageMetrics["max_response_time"] = data[index]["response_time"]
                
            #The satus code count
            status_code = data[index]["status_code"]
            if(status_code in averageMetrics["status_code_count"]):
                 averageMetrics["status_code_count"][status_code] += 1
            else:
                  averageMetrics["status_code_count"][status_code] = 1
                  
            #The response average size
            averageMetrics["size"] += data[index]["size"]
            
            index -= 1   
        averageMetrics["availibility"] = averageMetrics["availibility"]/float(len(data) - 1 - index)
        averageMetrics["response_time"] = averageMetrics["response_time"]/float(len(data) - 1 - index)
        averageMetrics["size"] = averageMetrics["size"]/float(len(data) - 1 - index)
    
    #Now we update the global varaible if the interval is 10min because the variable is only used for alert monitoring and we base that on the 10min average
    #(we need to lock it in case another thread tries to read or write a the same time)
    if(interval == 600):
        averageLock.acquire()
        websitesAverage[key] = averageMetrics
        averageLock.release()
    return averageMetrics

#Thread that deals with showing the metrics given a window, a refresh time and a time interval
class Interface(Thread):
    
    def __init__(self, window, wait, average_time):
        Thread.__init__(self)
        self.window = window
        self.wait = wait
        self.average_time = average_time
        
    def run(self):
        global runProgram
        global websitesMetrics
        global alertList
        global possiblyDown
        global verrou
        
        #runProgram is a global varaible which is set to false when user inputs "exit" which ends the program
        while runProgram:
            
            #Here we set the structure of the table
            self.window.clear()
            self.window.border("|","|","-","-","o","o","o","o")
            self.window.addstr(1,1,"Website")
            self.window.addstr(1,50,"Availibility")
            self.window.addstr(1,75,"Average res time (µs)")
            self.window.addstr(1,100,"Max res time")
            self.window.addstr(1,120,"Status Codes")
            self.window.addstr(1,160,"Response size (in bytes)")
            
            #We have to lock when we refresh so other threads dont refresh at the same time
            displayLock.acquire()
            self.window.refresh()
            displayLock.release()
            
            #We iterate over the global variable where all the metric data is stored in this format:
            #   {
            #       websiteUrl: 
            #           [{time:time when the request was made, response:True if the site was up, response_time:Time the res took, status_code:The status code},
            #           ...list of metrics],
            #       ...list of sites
            #   }
            #For each we get the average metric and show these on the current line which is the incremented
            #If a site falls below 80% availibility and is not already flagged as down we launch a thread checkDown to monitor it
            line = 2
            verrou.acquire()
            for key, value in websitesMetrics.items():
                average = getAverage(self.average_time, value, key)
                self.window.addstr(line,2, str(key))
                self.window.addstr(line,51, str(average["availibility"]))
                self.window.addstr(line,76, str(average["response_time"]))
                self.window.addstr(line,101, str(average["max_response_time"]))
                self.window.addstr(line,121, str(average["status_code_count"]))
                self.window.addstr(line,161, str(average["size"]))
                displayLock.acquire()
                self.window.refresh()
                displayLock.release()
                if(average["availibility"] < 0.8 and self.wait == 10 and not key in possiblyDown):
                    checkDown(key).start()
                line += 1
            verrou.release()
            stopSleep.wait(self.wait)

#This thread is called for each site that falls under 80% availibility
class checkDown(Thread):
    def __init__(self, url):
        Thread.__init__(self)
        self.key = url
        self.downtime =0
        self.uptime =0
    def run(self):
        global websitesAverage
        global runProgram
        global possiblyDown
        global passedAlertList
        global alertLock
        global alertLock2
        
        #We add the site to the list of possibly down sites
        possiblyDown[self.key] = True
        
        #We mark the start time se we can compare and see how long the site is down
        startTime = time.time()
        
        #While the site stays below 80% we check every 10 seconds how long it has been down
        #if it is more than 2 minutes we add it to the list of sites that are down
        while( websitesAverage[self.key]["availibility"]<0.8 and runProgram):
            self.downtime = time.time() - startTime
            if(self.downtime>120):
                alertLock.acquire()
                alertList[self.key] = [websitesAverage[self.key]["availibility"], self.downtime, True]
                alertLock.release()
            time.sleep(10)
            
        #If we exit the while before 2min have passed we remove the site from the possibly down list, the next while is skipped and the thread ends
        if(not self.key in alertList):
            possiblyDown.pop(self.key)
        
        #The site has gone over 80% at least once, we exit the while above and enter the one below
        #Now we check if it comes back online, so while it is still in the site down list we check if it stays above 80% for more thant 2 minutes
        while(self.key in alertList and runProgram):
            startUpTime = time.time()
            self.downtime = time.time() - startTime
            while(self.key in alertList and websitesAverage[self.key]["availibility"]>=0.8 and runProgram):
                self.uptime = time.time() - startUpTime
                self.downtime = time.time() - startTime
                
                #If the site stays over 80% for more than 2 minutes we pop the site from the alert list remove it from the possibly down list and add it to passed alerts
                if(self.uptime > 120):
                    alertLock.acquire()
                    alertList.pop(self.key)
                    alertLock.release()
                    alertLock2.acquire()
                    passedAlertList[uuid.uuid1()] = [self.key, self.downtime]
                    alertLock2.release()
                    possiblyDown.pop(self.key)
                    time.sleep(10)
                    
                #Otherwise we keep updating the alertlist on the state of the site with a different message (the False at the end)
                else:
                    alertLock.acquire()
                    alertList[self.key] = [websitesAverage[self.key]["availibility"], self.uptime, False]
                    alertLock.release()
                    time.sleep(10)
                    
            #If the site falls back below 80% we print the same message as before
            if(websitesAverage[self.key]["availibility"]<0.8):
                alertLock.acquire()
                alertList[self.key] = [websitesAverage[self.key]["availibility"], self.downtime, True]
                alertLock.release()
                
            time.sleep(10)

#This thread handles user input
class Input(Thread):
    def __init__(self, window):
        Thread.__init__(self)
        self.window = window
    
    def run(self):
        global runProgram
        global websitesMetrics
        while runProgram:
            self.window.clear()
            self.window.border("|","|","-","-","o","o","o","o")
            self.window.addstr(1, 1, "Enter website: (hit Enter to continue), type exit to exit")
            webwin = curses.newwin(1,85, 5,1)
            displayLock.acquire()
            self.window.refresh()
            displayLock.release()

            box = Textbox(webwin)
            webwin.clear()
            webwin.refresh()
            
            # User firsts inputs the site and submits it with Enter
            box.edit()
            website = box.gather()
            url = website[:-1]
            
            #If the user typed exit instead we change the global varaible runProgram to false to stop the program
            if(url == "exit"):
                runProgram =False
                stopSleep.set()
            #Otherwise we now ask the user to input a time interval in seconds between checks
            else:
                self.window.addstr(3, 1, "Enter interval in seconds: (hit Enter to send)")
                interwin = curses.newwin(1,30, 7,1)
                displayLock.acquire()
                self.window.refresh()
                displayLock.release()
                box2 = Textbox(interwin)            
                box2.edit()
                interval = box2.gather()  
                inter = interval[:-1]
                
                #Now we check the url is a valid fromat, that the site exists, that the interval is a number and the site is not already monitored
                if( validators.url(url)):
                    try:
                        requests.get(url)
                        try:
                            interval = float(inter)
                            verrou.acquire()
                            if(not str(url) in websitesMetrics):
                                
                                #If all goes well we launch a new thread to monitor this site with this interval
                                verrou.release()
                                monitor1 = Monitor(str(url), interval)
                                monitor1.start()
                            else:
                               verrou.release()
                               self.window.addstr(5,1,"Error: Site already monitored")
                               displayLock.acquire()
                               self.window.refresh()
                               displayLock.release()
                        except ValueError:
                            self.window.addstr(5,1,"Error: Interval NaN, (hit Enter to continue)")
                            displayLock.acquire()
                            self.window.refresh()
                            displayLock.release()
                            box.edit()
                    except requests.exceptions.ConnectionError:
                        self.window.addstr(5,1,"Error: Domain name does not exist, (hit Enter to continue)")
                        displayLock.acquire()
                        self.window.refresh()
                        displayLock.release()
                        box.edit()
                else:
                    self.window.addstr(5,1,"Error: Invalid url, (hit Enter to continue)")
                    displayLock.acquire()
                    self.window.refresh()
                    displayLock.release()
                    box.edit()

#This thread handles the alerts display            
class Alert(Thread):
    global alertList
    global runProgram
    global passedAlertList
    global alertLock
    global alertLock2
    def __init__(self, window, wait):
        Thread.__init__(self)
        self.window = window
        self.wait = wait
    def run(self):
        
        while runProgram:
            
            #We set the table
            self.window.clear()
            self.window.border("|","|","-","-","o","o","o","o")
            self.window.addstr(1, 1, "Current alerts:")
            self.window.addstr(8, 1, "Passed alerts:")
            displayLock.acquire()
            self.window.refresh()
            displayLock.release()
            
            #The start line is the line new alerts get printed on
            line = 2
            
            #We iterate over alert list to print each current alert
            alertLock.acquire()
            for key, value in alertList.items():
                
                #We print the alert in red if it under 80% and green if it is over 80%
                if(value[2]):
                    self.window.addstr(line, 1, "Website: %(website)s is at %(availibility)f availibility since %(time)i seconds" %{'website': key, 'availibility': value[0], 'time': int(value[1])},curses.color_pair(2))
                    displayLock.acquire()
                    self.window.refresh()
                    displayLock.release()
                else:
                    self.window.addstr(line, 1, "Website: %(website)s is back at %(availibility)f availibility since %(time)i seconds" %{'website': key, 'availibility': value[0], 'time': int(value[1])},curses.color_pair(3))
                    displayLock.acquire()
                    self.window.refresh()
                    displayLock.release()
                line += 1
            alertLock.release()
            
            #Same as before it indicates which line to start from
            passedAlertLine = 9
            
            #We iterate over the passed alert and print them
            alertLock2.acquire()
            for key, value in passedAlertList.items():
                self.window.addstr(passedAlertLine, 1, "Website: %(website)s was down for %(time)i seconds and is back up" %{'website': value[0], 'time': int(value[1])},curses.color_pair(2))
                displayLock.acquire()
                self.window.refresh()
                displayLock.release()
                passedAlertLine += 1
            alertLock2.release()
            
            time.sleep(self.wait)

#We create a thread for each site that we monitor
class Monitor(Thread):
    global runProgram
    def __init__(self, url, interval):
        Thread.__init__(self)
        self.url = url
        self.interval = interval
        
    def run(self):
        global runProgram
        global websitesMetrics
        
        #We add to the metrics dict a new item for this site
        verrou.acquire()
        websitesMetrics[self.url] = []
        verrou.release()
        end = 0
        
        #While the program runs we send and request between each interval and append the response in the website list in the metric dict
        #We do a try catch with a ConnectionError to catch timeouts, and add the custom metric
        #(Other errors than timeouts can raise an ConnectionError but I already eliminated errors raised by
        # wrong urls and nonexistant domains, it cleary is not a good solution but it works in nominal conditions)
        while runProgram:
            timeout = False
            try:
                r = requests.get(self.url, timeout=3)
            except requests.exceptions.ConnectionError:
                timeout = True
            if(not timeout):
                verrou.acquire()
                websitesMetrics[self.url].append({
                        "time": time.time(),
                        "response": r.ok,
                        "response_time": r.elapsed.microseconds,
                        "size": len(r.content),
                        "status_code": r.status_code
                })
                verrou.release()
            else:
                verrou.acquire()
                websitesMetrics[self.url].append({
                        "time": time.time(),
                        "response": False,
                        "response_time": 3000000,
                        "size": 0,
                        "status_code": 408
                })
                verrou.release()
            end += 1
            time.sleep(self.interval)

def main(stdscr):
    
    #We clear the console and initialze curses
    stdscr.clear()
    curses.init_pair(2, curses.COLOR_RED,curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_GREEN,curses.COLOR_BLACK)
    
    #We create each window we need, input, alert, and 2 for the metrics displays
    command_window = curses.newwin(10, 90, 3, 0)
    display_window = curses.newwin(20, 200, 20, 0)
    display_window_1min = curses.newwin(20, 200, 42, 0)
    alert_window = curses.newwin(15,100,3,100)
    stdscr.addstr(1,90, "Automated Site Metrics")
    stdscr.addstr(2,40, "Add new websites to monitor")
    stdscr.addstr(2,140, "Alerts")
    stdscr.addstr(19,90, "Site metrics for the past 10 min")
    stdscr.addstr(41,90, "Site metrics for the past hour")
    stdscr.refresh()
    
    #We launch the threads for user input, alert display, and metrics display
    interface = Interface(display_window,10,600)
    interface_1min = Interface(display_window_1min,60,3600)
    alerts = Alert(alert_window, 10)
    web_input = Input(command_window)
    interface.start()
    interface_1min.start()
    alerts.start()
    web_input.start()
    
    #We check for passed in arguments during launch
    #Here we check if a file is passed and we check that tha data received has the right format
    if(args.file):
        file = open(args.file, "r") 
        for line in file: 
            if(len(line.split()) == 2):
                url = line.split()[0]
                interval = line.split()[1]
                if( validators.url(url)):
                    try:
                        requests.get(url)
                        try:
                            inter = float(interval)
                            Monitor(url,inter).start()
                        except ValueError:
                            stdscr.addstr(61,90,"incorrect interval in file",curses.color_pair(2))
                            stdscr.refresh()
                    except requests.exceptions.ConnectionError:
                         stdscr.addstr(60,90, "incorrect urls in file loaded",curses.color_pair(2))
                         stdscr.refresh()
                else:
                    stdscr.addstr(60,90, "incorrect urls in file loaded",curses.color_pair(2))
                    stdscr.refresh()
                
    #Here we check for a site and an interval passed directly, we again chack the format                
    if(args.websites and args.intervals):
        monitor = Monitor(args.websites, args.intervals)
        monitor.start()
        url = args.websites
        interval = args.intervals
        if( validators.url(url)):
            try:
                requests.get(url)
                try:
                    inter = float(interval)
                    Monitor(url,inter).start()
                except ValueError:
                    stdscr.addstr(61,90,"incorrect interval in arg")
                    stdscr.refresh()
            except requests.exceptions.ConnectionError:
                 stdscr.addstr(60,90, "incorrect urls in arg")
                 stdscr.refresh()
        else:
            stdscr.addstr(60,90, "incorrect urls in arg")
            stdscr.refresh()
    
    #We wait for the threads to finish to properly close the program
    interface.join()
    interface_1min.join()
    web_input.join()
    
    curses.endwin()
    print("Program exited normally")

#Declare all globals variable such as ressource locks or dictionnaries where we store the data
verrou = RLock()
alertLock = RLock()
alertLock2 = RLock()
averageLock = RLock()
displayLock = RLock()
stopSleep = Event()
websitesMetrics = {
    #args.websites: []         
}
websitesAverage = {}
alertList = {}
passedAlertList = {}
possiblyDown = {}
runProgram = True

#The parser lets get arguments passed in at launch
parser = argparse.ArgumentParser(description='Pass websites and check intervals')
parser.add_argument('--websites', metavar='www', type=str, help='a web site')
parser.add_argument('--file', metavar='.txt', type=str, help='A file with sites and intervals')
parser.add_argument('--intervals', metavar='check', type=int, help='check intervals in seconds')
parser.add_argument('--test', type=int, help='test mode')
args = parser.parse_args()

os.system('clear')

#If a test flag has been set we run the test instead of the program
#First test is classic: site goes down for more than 2 min and comes back up directly
if(args.test == 1):
    print("test mode 1")
    key = "http://test.com"
    websitesAverage[key] = {
                "availibility": 0.7,
                "response_time": 132345,
                "size":12345,
                "max_response_time": 12345,
                "status_code_count": {}        
                }
    cmpt = 0
    startTime = time.time()
    checkDown("http://test.com").start()
    while cmpt < 300:
        print("----------Test started %i seconds ago----------" %int(time.time() - startTime))
        print("Is it possibly down (availibility<80% or down): %s  Should be: %s" %(str(key in possiblyDown), str(cmpt<250)))
        print("Is it down: %s   Should be: %s" %(str(key in alertList),str(cmpt>=120 and cmpt<250)))
        if(key in alertList):
            print("Is it recovering (availibility>0.8): %s  Should be: %s" %(str(not alertList[key][2]), str(cmpt>130)))
            print("Website: %(website)s is at %(availibility)f availibility since %(time)i seconds" %{'website': key, 'availibility': alertList[key][0], 'time': int(alertList[key][1])})
        if(cmpt <130):
            websitesAverage[key]["availibility"] = 0.7
            time.sleep(10)
        else:
            websitesAverage[key]["availibility"] = 1
            time.sleep(10)
        cmpt += 10
        
#Second test : site goes down (under 80% availibility) for > 2min, comes back up but less than 2min, goes down again, and then comes back up for good
elif(args.test == 2):
    print("test mode 2")
    key = "http://test.com"
    websitesAverage[key] = {
                "availibility": 0.7,
                "response_time": 132345,
                "size":12345,
                "max_response_time": 12345,
                "status_code_count": {}        
                }
    cmpt = 0
    startTime = time.time()
    checkDown("http://test.com").start()
    while cmpt < 340:
        print("----------Test started %i seconds ago----------" %int(time.time() - startTime))
        print("Is it possibly down (availibility<80% or down): %s  Should be: %s" %(str(key in possiblyDown), str(cmpt<320)))
        print("Is it down: %s   Should be: %s" %(str(key in alertList),str(cmpt>=120 and cmpt<320)))
        if(key in alertList):
            print("Is it recovering (availibility>0.8): %s  Should be: %s" %(str(not alertList[key][2]),str((cmpt>130 and cmpt<=160) or(cmpt>190))))
            print("Website: %(website)s is at %(availibility)f availibility since %(time)i seconds" %{'website': key, 'availibility': alertList[key][0], 'time': int(alertList[key][1])})
        if(cmpt <130):
            websitesAverage[key]["availibility"] = 0.7
            time.sleep(10)
        elif(cmpt<160):
            websitesAverage[key]["availibility"] = 1
            time.sleep(10)
        elif(cmpt<190):
            websitesAverage[key]["availibility"] = 0.7
            time.sleep(10)
        else:
            websitesAverage[key]["availibility"] = 1
            time.sleep(10)
        cmpt += 10
        
#Third test: site comes under 80% for less than two minutes and then stays up       
elif(args.test == 3):
    print("test mode 3")
    key = "http://test.com"
    websitesAverage[key] = {
                "availibility": 0.7,
                "response_time": 132345,
                "size":12345,
                "max_response_time": 12345,
                "status_code_count": {}        
                }
    cmpt = 0
    startTime = time.time()
    checkDown("http://test.com").start()
    while cmpt < 60:
        print("----------Test started %i seconds ago----------" %int(time.time() - startTime))
        print("Is it possibly down (availibility<80% or down): %s  Should be: %s" %(str(key in possiblyDown), str(cmpt<40)))
        print("Is it down: %s   Should be: %s" %(str(key in alertList),str(False)))
        
        if(cmpt <30):
            websitesAverage[key]["availibility"] = 0.7
            time.sleep(10)
        else:
            websitesAverage[key]["availibility"] = 1
            time.sleep(10)
        cmpt += 10
else:
    #Here we call our main function inside the curses.wrapper as it initialize the curses display
    curses.wrapper(main)







