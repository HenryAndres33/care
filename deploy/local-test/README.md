# Local test of the production stack

Runs the exact production compose on the laptop over plain HTTP at
http://localhost (port 80), with its own database and file store — it never
touches the development stack's data on 4000/9000.

    bash make-env.sh localhost
    sed -i 's|https://localhost|http://localhost|; s|https://files.localhost|http://files.localhost|' .env
    docker compose -f docker-compose.yml -f local-test/compose.override.yml up -d --build

Tear down (deletes the test database and files):

    docker compose -f docker-compose.yml -f local-test/compose.override.yml down -v
