How to get cert chain
=====================

``openssl s_client -showcerts -connect smtp.gmail.com:587 -starttls smtp``

``openssl s_client -showcerts -connect imap.gmail.com:993``


Show cert info
==============

``xsel | openssl x509 -in - -text``
