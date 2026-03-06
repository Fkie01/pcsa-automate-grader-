#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main() {
    printf("Content-Type: text/plain\r\n\r\n");
    char *method = getenv("REQUEST_METHOD");
    char *query  = getenv("QUERY_STRING");
    char *length = getenv("CONTENT_LENGTH");
    printf("METHOD=%s\n", method ? method : "(null)");
    printf("QUERY=%s\n",  query  ? query  : "(empty)");
    printf("LENGTH=%s\n", length ? length : "(null)");
    if (length && atoi(length) > 0) {
        int len = atoi(length);
        char *buf = malloc(len + 1);
        if (buf) {
            fread(buf, 1, len, stdin);
            buf[len] = 0;
            printf("BODY=%s\n", buf);
            free(buf);
        }
    }
    return 0;
}
