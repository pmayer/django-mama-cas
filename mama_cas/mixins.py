import logging

from django.conf import settings
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import SiteProfileNotAvailable

from mama_cas.models import ServiceTicket
from mama_cas.models import ProxyTicket
from mama_cas.models import ProxyGrantingTicket
from mama_cas.exceptions import InvalidRequestError
from mama_cas.exceptions import InvalidTicketError
from mama_cas.exceptions import InvalidServiceError
from mama_cas.exceptions import InternalError
from mama_cas.exceptions import BadPGTError


LOG = logging.getLogger('mama_cas')


class NeverCacheMixin(object):
    """
    View mixin that disables caching
    """
    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super(NeverCacheMixin, self).dispatch(request, *args, **kwargs)

class TicketValidateMixin(object):
    """
    View mixin providing ticket validation methods.
    """
    def validate_service_ticket(self, request):
        """
        Given a ``request``, validate a service ticket string. On success, a
        triplet is returned containing the ``ServiceTicket`` and an optional
        ``ProxyGrantingTicket``, with no error. On error, a triplet is
        returned containing no ``ServiceTicket`` or ``ProxyGrantingTicket``,
        but with an ``Error`` describing what went wrong.
        """
        service = request.GET.get('service')
        ticket = request.GET.get('ticket')
        renew = request.GET.get('renew')
        pgturl = request.GET.get('pgtUrl')

        LOG.debug("Service validation request received for %s" % ticket)
        try:
            st = ServiceTicket.objects.validate_ticket(ticket,
                                                       service=service,
                                                       renew=renew)
        except (InvalidRequestError, InvalidTicketError,
                InvalidServiceError, InternalError) as e:
            LOG.warn("%s %s" % (e.code, e))
            return None, None, e
        else:
            if pgturl:
                LOG.debug("Proxy-granting ticket request received for %s" % pgturl)
                pgt = ProxyGrantingTicket.objects.create_ticket(pgturl,
                                                                user=st.user,
                                                                granted_by_st=st)
            else:
                pgt = None
            return st, pgt, None

    def validate_proxy_ticket(self, request):
        """
        Given a ``request``, validate a proxy ticket string. On success, a
        4-tuple is returned containing the ``ProxyTicket``, a list of all
        services that proxied authentication and an optional
        ``ProxyGrantingTicket``, with no error. On error, a triplet is
        returned containing no ``ProxyTicket`` or ``ProxyGrantingTicket``,
        but with an ``Error`` describing what went wrong.
        """
        service = request.GET.get('service')
        ticket = request.GET.get('ticket')
        pgturl = request.GET.get('pgtUrl')

        LOG.debug("Proxy validation request received for %s" % ticket)
        try:
            pt = ProxyTicket.objects.validate_ticket(ticket,
                                                     service=service)
        except (InvalidRequestError, InvalidTicketError,
                InvalidServiceError, InternalError) as e:
            LOG.warn("%s %s" % (e.code, e))
            return None, None, None, e
        else:
            # Build a list of all services that proxied authentication,
            # in reverse order of which they were traversed
            proxies = [pt.service]
            prior_pt = pt.granted_by_pgt.granted_by_pt
            while prior_pt:
                proxies.append(prior_pt.service)
                prior_pt = prior_pt.granted_by_pgt.granted_by_pt

            if pgturl:
                LOG.debug("Proxy-granting ticket request received for %s" % pgturl)
                pgt = ProxyGrantingTicket.objects.create_ticket(pgturl,
                                                                user=pt.user,
                                                                granted_by_pt=pt)
            else:
                pgt = None
            return pt, pgt, proxies, None

    def validate_proxy_granting_ticket(self, request):
        """
        Given a ``request``, validate a proxy granting ticket string. On
        success, an ordered pair is returned containing a ``ProxyTicket``,
        with no error. On error, an ordered pair is returned containing no
        ``ProxyTicket``, but with an ``Error`` describing what went wrong.
        """
        pgt = request.GET.get('pgt')
        target_service = request.GET.get('targetService')

        LOG.debug("Proxy ticket request received")
        try:
            pgt = ProxyGrantingTicket.objects.validate_ticket(pgt,
                                                              target_service)
        except (InvalidRequestError, BadPGTError, InternalError) as e:
            LOG.warn("%s %s" % (e.code, e))
            return None, e
        else:
            pt = ProxyTicket.objects.create_ticket(service=target_service,
                                                   user=pgt.user,
                                                   granted_by_pgt=pgt)
            return pt, None

class CustomAttributesMixin(object):
    """
    View mixin for including custom user attributes in a validation response.
    """
    def get_custom_attributes(self, ticket):
        """
        Given a ``ticket``, build a list of user attributes from either the
        ``User`` or user profile object to be returned with a validation
        success. The attributes are selected with two settings variables:

        ``MAMA_CAS_USER_ATTRIBUTES``
            This is a list of name and ``User`` attribute pairs. The name can
            be any meaningful string, while the attribute must correspond with
            an attribute on the ``User`` object.

        ``MAMA_CAS_PROFILE_ATTRIBUTES``
            This is a list of name and user profile attribute pairs. The name
            can be any meaningful string, while the attribute must correspond
            with an attribute on the user profile object.

        One or both of the settings variables may be used, with all data
        returned as a single list. Ordering is not guaranteed.
        """
        if not ticket:
            return None
        user = ticket.user
        attributes = []

        user_attr_list = getattr(settings, 'MAMA_CAS_USER_ATTRIBUTES', ())
        for (name, key) in user_attr_list:
            try:
                attribute = [name, getattr(user, key)]
            except AttributeError:
                LOG.warn("User has no attribute named '%s'" % key)
            else:
                attributes.append(attribute)

        try:
            profile = user.get_profile()
        except (ObjectDoesNotExist, SiteProfileNotAvailable):
            pass
        else:
            profile_attr_list = getattr(settings, 'MAMA_CAS_PROFILE_ATTRIBUTES', ())
            for (name, key) in profile_attr_list:
                try:
                    attribute = [name, getattr(profile, key)]
                except AttributeError:
                    LOG.warn("Profile has no attribute named '%s'" % key)
                else:
                    attributes.append(attribute)

        return attributes
