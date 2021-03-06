# -*- coding: latin-1 -*-

''' 
 Freeciv - Copyright (C) 2009 - Andreas R�sdal   andrearo@pvv.ntnu.no
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2, or (at your option)
   any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
'''

import string, time
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import logging
from civcom import *
from debugging import *
from SocketServer import ThreadingMixIn
from urlparse import urlparse

logger = logging.getLogger("freeciv-proxy");

MAX_WAIT_TIME = 1.0;  # max wait time for packets from civserver.
INC_WAIT_TIME = 0.005  # waiting time betweeen checking for updates.
PACKET_SIZE_WAIT_THRESHOLD = 4;
REQ_QUEUE_SIZE = 70;
FC_HTTP_TIMEOUT = 40;
MAX_PACKET_LEN = 65536;

class CivWebServer(ThreadingMixIn, HTTPServer):

  def __init__(self, server_address, RequestHandlerClass):
    self.civcoms = {};
    self.request_queue_size = REQ_QUEUE_SIZE;
    self.timeout = FC_HTTP_TIMEOUT;
    self.daemon_threads = True;
    HTTPServer.__init__(self, server_address, RequestHandlerClass);

  # get the civcom instance which corresponds to the requested user.
  def get_civcom(self, username, civserverport, client_ip):
    key = username + str(civserverport);
    if key not in self.civcoms.keys():
      civcom = CivCom(username, int(civserverport));
      civcom.client_ip = client_ip;
      civcom.connect_time = time.time();
      civcom.set_civwebserver(self);
      civcom.start();
      self.civcoms[key] = civcom;
      return civcom;
    else:
      usrcivcom = self.civcoms[key];
      if (usrcivcom.client_ip != client_ip):
        logger.error("Unauthorized connection from client IP: " + client_ip);
        return None;
      return usrcivcom;

class WebserverHandler(BaseHTTPRequestHandler):

    def do_GET(self):
      if (self.path.endswith("status")):
        try:
          self.send_response(200);
          self.send_header('Content-type',    'text/html')
          self.end_headers()
          self.wfile.write(get_debug_info(self.server.civcoms));
        except:
          self.send_error(503, "Service Unavailable.");        
      else:
        self.send_error(500, "Invalid request. HTTP GET not supported.");
      return

    def do_POST(self):        
        url = urlparse(self.path);
        params = dict([part.split('=') for part in url[4].split('&')])
        username = params["u"];
        civserverport = params["p"];
        
        # check if session is valid.
        if (username == None or civserverport == None 
            or username == 'null' or civserverport == 'null'): 
            self.send_error(500, "Invalid session");
            return;

        client_ip = "unknown";
	# x-forwarded-for is a header containing the client's IP address.
        if self.headers.dict.has_key("x-forwarded-for"):
          client_ip = self.headers.dict["x-forwarded-for"]

        # get the civcom instance which corresponds to this user.        
        civcom = self.server.get_civcom(username, civserverport, client_ip);

        if (civcom == None):
          self.send_error(503, "Could not authenticate user.");
          return;

        try:
          # parse request from webclient
          content_length = 0;
          if self.headers.dict.has_key("content-length"):
            content_length = string.atoi(self.headers.dict["content-length"])
            
            if content_length > 100000:
              self.send_error(503,'Civserver communication failure: size exceeded.')
              return;
            
            post_data = self.rfile.read(content_length)
            
            # send JSON request to civserver.
	    if (post_data != "[]" and len(post_data) < MAX_PACKET_LEN):
              if not civcom.send_packets_to_civserver(post_data):
                if (logger.isEnabledFor(logging.INFO)):
                  logger.info("Sending data to civserver failed.");
                self.send_error(503,'Civserver communication failure: %s' % self.path)
                return;
 
  	  response_payload = civcom.get_send_result_string();

          if (PACKET_SIZE_WAIT_THRESHOLD < content_length):
            # If client sent packet to server, then wait for server response.
            # Sleep after packets are sent to civserver, to allow 
            # time for packets to arrive at this proxy.
            # This will speed up client server latency.
	    start_time = time.time();
	    while (len(response_payload) < PACKET_SIZE_WAIT_THRESHOLD \
		   and time.time() - start_time < MAX_WAIT_TIME):
              time.sleep(INC_WAIT_TIME);
              response_payload = civcom.get_send_result_string();

          # prepare reponse to webclient.
	  response_data = "".join(["HTTP/1.1 200 OK\nContent-Length: " + str(len(response_payload)), \
                                  "\nContent-Type: text/html\n\n", response_payload]);

          self.wfile.write(response_data);
        except Exception, e:
          if (logger.isEnabledFor(logging.ERROR)):
            logger.error(e);
          self.send_error(503, "Service Unavailable. Something is wrong here ");

