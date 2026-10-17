// Twilio Function for the Linphone line.
//   call from Linphone (To is a sip: address) -> dial that phone number, showing the Twilio number
//   call to the Twilio number                 -> ring Linphone
exports.handler = function (context, event, callback) {
  const twiml = new Twilio.twiml.VoiceResponse();
  const fromApp = /^sip:([^@]+)@/.exec(event.To || '');
  if (fromApp) {
    let number = decodeURIComponent(fromApp[1]).replace(/[^\d+]/g, '');
    if (number.startsWith('00')) number = '+' + number.slice(2);
    if (!number.startsWith('+')) number = '+' + number;
    twiml.dial({ callerId: context.CALLER_ID, answerOnBridge: true }).number(number);
  } else {
    twiml.dial({ answerOnBridge: true, timeout: 30 }).sip(`sip:${context.SIP_USER}@${context.SIP_DOMAIN}`);
  }
  callback(null, twiml);
};
