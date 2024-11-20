#!/usr/bin/env python
#
# Copyright (C) 2024 SIP Point Consulting SRL
# Copyright (C) 2024 ConnexCS (Worldwide) Ltd
#
# This file is part of the OpenSIPS AI Voice Connector project
# (see https://github.com/OpenSIPS/opensips-ai-voice-connector-ce).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

""" Main module that starts the Deepgram AI integration """

import signal
import asyncio
import logging
import secrets
import string

from sip import SIPMessage
from opensips.mi import OpenSIPSMI, OpenSIPSMIException
from opensips.event import OpenSIPSEventHandler, OpenSIPSEventException
from aiortc.sdp import SessionDescription

from call import Call
from config import Config
from codec import UnsupportedCodec
from utils import get_ai_flavor, indialog, UnknownSIPUser

mi_cfg = Config.get("engine")

SERVER_IP = mi_cfg.get("ip", "IP", "0.0.0.0")
SERVER_PORT = int(mi_cfg.get("port", "PORT", "8080"))
ADV_IP = mi_cfg.get("ip", "IP", "0.0.0.0")

class AsyncSIPServerProtocol(asyncio.Protocol):
	"""
	Asynchronous SIP server protocol to handle incoming SIP messages over TCP.
	"""
	def __init__(self):
		self.transport = None
		self.client_address = None
		self.did = generate_unique_string()
		self.currentCall = None

	def connection_made(self, transport):
		# TODO: Send a BYE message when hanging up from Server Side
		self.transport = transport
		self.client_address = transport.get_extra_info('peername')
		print(f"Connection established with {self.client_address}")

	def data_received(self, data):
		"""
		Handle incoming TCP data.
		"""
		print(f"Received data from {self.client_address}")
		try:
			# Parse the incoming SIP message
			message = SIPMessage(data)
		except Exception as e:
			print(f"Failed to parse SIP message: {e}")
			self.transport.close()
			return

		# Determine the response based on the SIP method
		if message.method == "INVITE":
			## Generate a Dialog ID

			response = self.create_response(message, '100 Trying')
			self.transport.write(response.encode('utf-8'))

			headers, body = data.split(b"\r\n\r\n")

			oldSDP = body.decode('utf-8')
			# remove rtcp line, since the parser throws an error on it
			sdp_str = "\n".join([line for line in oldSDP.split("\n")
									if not line.startswith("a=rtcp:")])

			print(type(sdp_str), sdp_str)
			sdp = SessionDescription.parse(sdp_str)

			# If existing call, we should handle hold and unhold in SDP
			# if call:
			# 	# handle in-dialog re-INVITE
			# 	direction = sdp.media[0].direction
			# 	# if not direction or direction == "sendrecv":
			# 	# 	call.resume()
			# 	# else:
			# 	# 	call.pause()
			# 	try:
			# 		mi_reply(key, method, 200, 'OK', call.get_body())
			# 	except OpenSIPSMIException:
			# 		logging.exception("Error sending response")
			# 	return

			try:
				# TODO Make this all dynamic, reading from JWT
				self.currentCall = Call(self.did, sdp, get_ai_flavor(params))
				# sdp = self.generate_sdp()
				sdp = self.currentCall.get_body()
				response = self.create_response(message, "200 OK", sdp)

			except Exception as e:  # pylint: disable=broad-exception-caught
				logging.exception("Error creating call %s", e)
				response = self.create_response(message, "500 Internal Server Error")

			## await asyncio.sleep(0.05)

		elif message.method == "OPTIONS":
			response = self.create_response(message, "200 OK")
			# TODO: Differentiate in dialog and out of dialog pings
		elif message.method == "BYE":
			# TODO: Tear down the call
			response = self.create_response(message, "200 OK")
		elif message.method == "ACK":
			print('Recieved ACK')
			# Do nothing for ACK messages / Consider Session Fully Established
			# TODO: Timer for missing ACK and terminate the call.
		else:
			response = self.create_response(message, "501 Not Implemented")

		# Send the response back to the client
		self.transport.write(response.encode('utf-8'))
		print(f"Sent response to {self.client_address}")

	def create_response(self, request, status_line, body = ''):
		response_lines = [
			f"SIP/2.0 {status_line}",
			'Via: ' + request.headers.get("Via")[0]['type'] + ' ' + ':'.join(request.headers.get("Via")[0]['address']) + ':branch=' + request.headers.get("Via")[0]['branch'],
			'To: ' + request.headers.get("To")['raw'] + ';tag=' + self.did,
			'From: ' + request.headers.get("From")['raw'],
			'Call-ID: ' + request.headers.get("Call-ID"),
			'Contact:' + f' <sip:ai@{ADV_IP};transport=tcp>',
			'CSeq: ' + request.headers.get("CSeq")['check'] + ' ' + request.headers.get("CSeq")['method'],
			'Allow: INVITE, ACK, CANCEL, OPTIONS, BYE',
			"Content-Length: " + str(len(body)),
			"",  # End headers with an empty line
			body
		]

		print('DATA', "\r\n".join(response_lines))
		return "\r\n".join(response_lines)

	def generate_sdp(self):
		"""
		Generate an SDP response for the SIP INVITE request. (Currently hardcoded to port 1234)
		# TODO: Implement dynamic port allocation for SDP Endpoint
		"m=audio 1234 RTP/AVP 0", - 1234 means the port number 0 means ULAW codec (8 = alaw)
		"a=rtpmap:0 PCMU/8000",

		"m=audio 1234 RTP/AVP 0 8", - Modification to add alaw support in
		"a=rtpmap:0 PCMA/8000", - Add this line in for ALAW support along with ULAW
		"""
		sdp = (
			"v=0",
			f"o=- 0 0 IN IP4 {ADV_IP}",
			"s=AI Voice Connector",
			f"c=IN IP4 ADV_IP",
			"t=0 0",
			"m=audio 1234 RTP/AVP 0",
			"a=rtpmap:0 PCMU/8000",
			"a=sendrecv",
			""
		)
		return "\r\n".join(sdp)

	def connection_lost(self, exc):
		"""
		Handle the loss of connection.
		"""
		print(f"Connection closed with {self.client_address}")


async def async_run():
	# Create the asyncio TCP server
	loop = asyncio.get_running_loop()

	# Create the server
	server = await loop.create_server(
		lambda: AsyncSIPServerProtocol(),
		SERVER_IP,
		SERVER_PORT
	)

	print(f"SIP TCP server running on {SERVER_IP}:{SERVER_PORT}. Press Ctrl+C to stop.")
	try:
		await server.serve_forever()
	except KeyboardInterrupt:
		print("\nSIP TCP server shutting down.")
		server.close()
		await server.wait_closed()

def run():
	""" Runs the entire engine asynchronously """
	asyncio.run(async_run())

def generate_unique_string(length=8):
	characters = string.ascii_letters + string.digits
	unique_string = ''.join(secrets.choice(characters) for _ in range(length))
	return unique_string