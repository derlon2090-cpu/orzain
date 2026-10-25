FROM nginx:alpine
COPY infrastructure/nginx/watch-cert.sh /usr/local/bin/watch-cert.sh
RUN chmod 0755 /usr/local/bin/watch-cert.sh
CMD ["/usr/local/bin/watch-cert.sh"]
